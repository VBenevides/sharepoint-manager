"""
Module used to interact with sharepoint sites using an approach similar to file systems
"""

# ---------------------------------------------------------------------- #
# Imports
# ---------------------------------------------------------------------- #

import base64
import logging
import os
import re
import shutil
import threading
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import requests
from msal import ConfidentialClientApplication, PublicClientApplication

from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPDeletedItem,
    SPFile,
    SPFolder,
    TokenProvider,
    UserDelegatedCredential,
)
from .exceptions import (
    SPDriveNotFound,
    SPFileNotFound,
    SPFolderNotEmpty,
    SPFolderNotFound,
    SPUnauthorizedTarget,
    SPValidationError,
)
from .utils import (
    get_filename,
    get_names_to_folder,
    parse_www_authenticate,
    quote_path,
    quote_segment,
    safe_join,
)

logger = logging.getLogger(__name__)

# Conservative throttling of progress log lines to avoid flooding stdout for
# multi-GB transfers.
_PROGRESS_LOG_INTERVAL_SEC = 2.0
# Statuses that should be retried in addition to 429.
_RETRY_STATUSES = (429, 500, 502, 503, 504)
_RETRY_METHODS = {"GET", "HEAD", "OPTIONS", "PUT"}
# Microsoft Graph upload chunks must be a multiple of 320 KiB.
_GRAPH_CHUNK_UNIT = 327680
# ~6.25 MiB upload chunk – within Graph's 5–10 MiB recommendation.
_UPLOAD_CHUNK_SIZE = 20 * _GRAPH_CHUNK_UNIT
# Streaming download chunk size.
_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024
# GUID pattern used to extract a tenant id from authorization URIs.
_GUID_RE = re.compile(r"/([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})")
_GRAPH_HOSTS = {
    "graph.microsoft.com",
    "graph.microsoft.us",
    "dod-graph.microsoft.us",
    "graph.microsoft.de",
    "microsoftgraph.chinacloudapi.cn",
}
_MICROSOFT_CAPABILITY_SUFFIXES = (
    ".sharepoint.com",
    ".sharepoint.us",
    ".sharepoint.de",
    ".sharepoint.cn",
    ".sharepoint-mil.us",
    ".sharepoint-df.com",
    ".1drv.com",
)
_SHAREPOINT_SUFFIXES = tuple(
    x for x in _MICROSOFT_CAPABILITY_SUFFIXES if x != ".1drv.com"
)


class SharepointManagerBase:
    """
    Base class for Sharepoint Manager Classes
    """

    credentials: ClientCredential | UserDelegatedCredential
    ca: ConfidentialClientApplication | PublicClientApplication
    _session: requests.Session
    tenant_url: str

    # Per-instance lock guarding the token cache. Initialised lazily so the
    # base class works for subclasses that don't call ``__init__`` themselves.
    _token_lock: threading.Lock

    def _get_token_lock(self) -> threading.Lock:
        lock = getattr(self, "_token_lock", None)
        if lock is None:
            # Race here is benign – worst case two locks are created and the
            # one stored last wins. The very first cached token write will
            # still be observed by readers thanks to the GIL semantics.
            lock = threading.Lock()
            self._token_lock = lock
        return lock

    # ----------------------------------------------------------
    # Support Methods
    # ----------------------------------------------------------

    def _hdr(self, json_content: bool = False) -> dict[str, Any]:
        token = self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _validate_graph_url(self, url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.hostname != self.graph_host
            or parsed.port not in (None, 443)
        ):
            raise SPValidationError(
                "Authenticated requests require the configured HTTPS Graph host"
            )

    def _validate_capability_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in (None, 443)
            or not (
                host == self.graph_host
                or any(
                    host.endswith(suffix) for suffix in _MICROSOFT_CAPABILITY_SUFFIXES
                )
            )
        ):
            raise SPValidationError(
                "Capability URLs must use an approved HTTPS Microsoft host"
            )

    @staticmethod
    def _validate_sharepoint_url(url: str) -> Any:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in (None, 443)
            or not any(host.endswith(suffix) for suffix in _SHAREPOINT_SUFFIXES)
            or not ("/sites/" in parsed.path or "/teams/" in parsed.path)
        ):
            raise SPValidationError(
                "SharePoint URLs must use an approved HTTPS site host"
            )
        return parsed

    def _validate_item_boundary(self, item: dict[str, Any]) -> None:
        parent = item.get("parentReference")
        if not isinstance(parent, dict):
            raise SPUnauthorizedTarget("Resolved item has no trusted parent reference")
        drive_id = parent.get("driveId")
        site_id = parent.get("siteId")
        if drive_id != self._drive_id or (
            site_id is not None and site_id != self._site_id
        ):
            raise SPUnauthorizedTarget(
                "Resolved item is outside the configured SharePoint boundary"
            )

    def _validate_object_boundary(self, obj: SPFile | SPFolder) -> None:
        drive_id = obj.parent_reference.get("driveId")
        if drive_id != self._drive_id:
            raise SPUnauthorizedTarget(
                "File is outside the configured SharePoint boundary"
            )

    def _validate_file_boundary(self, file: SPFile) -> None:
        self._validate_object_boundary(file)

    def _get_tenant_id(self) -> str:
        """Retrieve the tenant ID from the SharePoint tenant URL."""

        r = self._request(
            "HEAD",
            self.tenant_url,
            headers={"Authorization": "Bearer"},
            timeout=20,
            authenticated=False,
        )
        params = parse_www_authenticate(r.headers.get("WWW-Authenticate", ""))
        realm = params.get("bearer realm") or params.get("realm")
        if realm:
            return realm
        # Some tenants return ``authorization_uri=https://login.microsoftonline.com/{tid}``
        auth_uri = params.get("authorization_uri") or params.get("authorization")
        if auth_uri:
            m = _GUID_RE.search(auth_uri)
            if m:
                return m.group(1)

        raise RuntimeError("Cannot determine tenant id from WWW-Authenticate header")

    def _ensure_token(self) -> str:
        # Fast path – lock-free read of the cached token.
        now = int(time.time())
        cached_token = getattr(self, "_cached_token", None)
        cached_expiry = int(getattr(self, "_cached_token_expiry", 0))
        if cached_token and (cached_expiry - now) > 120:
            return str(cached_token)

        # Slow path – only one thread per instance refreshes the token.
        with self._get_token_lock():
            # Re-check inside the lock so concurrent callers wait once and
            # then reuse the freshly cached token.
            now = int(time.time())
            cached_token = getattr(self, "_cached_token", None)
            cached_expiry = int(getattr(self, "_cached_token_expiry", 0))
            if cached_token and (cached_expiry - now) > 120:
                return str(cached_token)

            # Acquire a new token. Injected providers cover managed identity,
            # workload federation, certificates, and application-specific flows.
            if self._token_provider is not None:
                result = self._token_provider.get_token(
                    f"https://{self.graph_host}/.default"
                )
                token_value = getattr(result, "token", result)
                expires_on = int(getattr(result, "expires_on", 0) or 0)
                if isinstance(result, dict):
                    token_value = result.get("access_token", result.get("token"))
                    expires_on = int(result.get("expires_on", 0) or 0)
                result = {
                    "access_token": token_value,
                    "expires_on": expires_on,
                    "expires_in": max(expires_on - now, 60) if expires_on else 3600,
                }
            elif isinstance(self.ca, PublicClientApplication):
                warnings.warn(
                    "UserDelegatedCredential/ROPC is deprecated; inject a delegated token provider",
                    DeprecationWarning,
                    stacklevel=2,
                )
                result = self.ca.acquire_token_by_username_password(
                    username=self.credentials.username,  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                    password=self.credentials.password,  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                    scopes=[f"https://{self.graph_host}/.default"],
                )
            else:
                result = self.ca.acquire_token_for_client(
                    scopes=[f"https://{self.graph_host}/.default"]
                )

            if not isinstance(result, dict) or "access_token" not in result.keys():
                raise RuntimeError("Authentication failed")

            token = str(result["access_token"])
            # Prefer 'expires_on' (epoch seconds as str) else compute from 'expires_in'
            try:
                expires_on = int(result.get("expires_on", 0))
            except Exception:
                expires_on = 0
            if not expires_on:
                try:
                    expires_in = int(result.get("expires_in", 3600))
                except Exception:
                    expires_in = 3600
                expires_on = now + max(expires_in, 60)

            # Cache the token and its expiry
            self._cached_token = token
            self._cached_token_expiry = int(expires_on)

            return token

    # ----------------------------------------------------------
    # Internal HTTP helpers
    # ----------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int | tuple[int, int] | None = 30,
        json: Any | None = None,
        data: Any | None = None,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        max_attempts: int = 5,
        authenticated: bool | None = None,
        allow_redirects: bool | None = None,
    ) -> requests.Response:
        if authenticated is None:
            authenticated = bool(
                headers and str(headers.get("Authorization", "")).startswith("Bearer ")
            )
        if authenticated:
            self._validate_graph_url(url)
        elif url != self.tenant_url:
            self._validate_capability_url(url)
        if allow_redirects is None:
            allow_redirects = not authenticated
        method = method.upper()
        retryable = method in _RETRY_METHODS
        attempt = 1
        policy = getattr(self, "policy", OperationPolicy())
        max_attempts = min(max_attempts, policy.max_retry_attempts) if retryable else 1
        deadline = time.monotonic() + policy.wall_clock_seconds
        while True:
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    json=json,
                    data=data,
                    params=params,
                    stream=stream,
                    allow_redirects=allow_redirects,
                )
            except requests.RequestException:
                if (
                    not retryable
                    or attempt >= max_attempts
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(min(2**attempt, policy.max_retry_after_seconds))
                attempt += 1
                continue
            if authenticated and 300 <= resp.status_code < 400:
                resp.close()
                raise SPValidationError("Authenticated Graph redirects are not allowed")
            request_id = resp.headers.get("request-id") or resp.headers.get(
                "client-request-id"
            )
            if request_id:
                logger.debug(
                    "Graph request status=%s request_id=%s",
                    resp.status_code,
                    request_id,
                )
            # Handle throttling / transient 5xx with Retry-After.
            if resp.status_code in _RETRY_STATUSES and attempt < max_attempts:
                retry_after = resp.headers.get("Retry-After")
                delay: float | None = None
                if retry_after is not None:
                    try:
                        delay = float(int(retry_after))
                    except (TypeError, ValueError):
                        # Fall back to HTTP-date format.
                        try:
                            target = parsedate_to_datetime(retry_after)
                            delay = max(0.0, target.timestamp() - time.time())
                        except (TypeError, ValueError):
                            delay = None
                if delay is None:
                    delay = float(min(2**attempt, 60))
                delay = min(
                    delay,
                    policy.max_retry_after_seconds,
                    max(0.0, deadline - time.monotonic()),
                )
                # Release any streamed body before sleeping to avoid leaking
                # connections back to the pool in CLOSE_WAIT.
                try:
                    resp.close()
                except Exception:
                    pass
                time.sleep(delay)
                attempt += 1
                continue
            return resp

    def _paginate(self, url: str) -> Iterator[dict[str, Any]]:
        """Yield items across Graph pages following @odata.nextLink."""
        next_url: str | None = url
        seen: set[str] = set()
        page_count = 0
        item_count = 0
        while next_url:
            if page_count >= getattr(self, "policy", OperationPolicy()).max_pages:
                raise SPValidationError("Graph page budget exceeded")
            if next_url in seen:
                raise SPValidationError("Repeated Graph pagination link")
            seen.add(next_url)
            self._validate_graph_url(next_url)
            r = self._request(
                "GET", next_url, headers=self._hdr(), timeout=30, authenticated=True
            )
            r.raise_for_status()
            data = r.json()
            page_count += 1
            for item in data.get("value", []):
                item_count += 1
                if item_count > getattr(self, "policy", OperationPolicy()).max_items:
                    raise SPValidationError("Graph item budget exceeded")
                yield item
            next_url = data.get("@odata.nextLink")


class SharepointManager(SharepointManagerBase):
    """
    Provides an interface for interacting with a SharePoint site.


    Supports uploading, downloading, listing, and deleting files/folders
    using Microsoft Graph API.


    Examples
    --------
    >>> creds = ClientCredential("app_id", "app_secret")
    >>> manager = SharepointManager(
    ... sharepoint_site_url="https://my_tenant.sharepoint.com/sites/my_site",
    ... credentials=creds,
    ... )
    >>> manager.download_file(
    ... file="file.txt",
    ... local_download_path="./Download_Dir",
    ... sp_relative_folder_path="Folder/Subfolder"
    ... )
    >>> manager.upload_file(
    ... local_file_path="./Download_Dir/file.txt",
    ... sp_relative_folder_path="Folder/Subfolder2"
    ... )
    """

    def __init__(
        self,
        sharepoint_site_url: str,
        credentials: ClientCredential | UserDelegatedCredential | None = None,
        document_folder_name: str | None = None,
        graph_host: str = "graph.microsoft.com",
        tenant_id: str | None = None,
        token_provider: TokenProvider | None = None,
        policy: OperationPolicy | None = None,
    ) -> None:
        """
        Initializes the SharepointManager with a given SharePoint URL and credentials.

        Parameters
        ----------
        sharepoint_site_url : str
            The URL of the SharePoint site. E.g: 'https://{tenant_url}.sharepoint.com/sites/{site_name}'.
        credentials : ClientCredential
            Graph API credentials for authentication.
        document_folder_name : str, optional
            The name of the document folder (drive) in the SharePoint site. If ``None``
            (default), the site's default document library is auto-detected via the
            Microsoft Graph ``/drive`` endpoint and its name is resolved from the
            returned ``webUrl``.

            This is vital to guarantee that the class will be able to find the documents in the site.

        Returns
        -------
        None

        Examples
        --------
        >>> user_cred = ClientCredential("graph_id", "graph_secret") # Don't hardcode passwords
        >>> manager = SharepointManager(sharepoint_site_url = "https://my_tenant.sharepoint.com/sites/my_site",
        >>>     credentials = user_cred,
        >>> )
        """

        parsed_site_url = self._validate_sharepoint_url(sharepoint_site_url)
        if credentials is None and token_provider is None:
            raise ValueError("credentials or token_provider is required")
        self.graph_host = graph_host.lower().rstrip(".")
        if self.graph_host not in _GRAPH_HOSTS:
            raise SPValidationError(
                "graph_host must be an approved Microsoft Graph host"
            )
        self._graph_base_url = f"https://{self.graph_host}/v1.0"
        self.policy = policy or OperationPolicy()
        self._session: requests.Session = requests.Session()
        self.credentials = credentials
        self._token_provider = token_provider

        self.url: str = sharepoint_site_url
        if "/teams/" in parsed_site_url.path:
            self.site_separator: Literal["/teams/", "/sites/"] = "/teams/"
        elif "/sites/" in parsed_site_url.path:
            self.site_separator = "/sites/"
        else:
            raise ValueError(
                "sharepoint_site_url must contain '/sites/' or '/teams/'. "
                f"Got: {sharepoint_site_url!r}"
            )
        self.tenant_url: str = f"{parsed_site_url.scheme}://{parsed_site_url.netloc}"
        self.tenant_id: str = tenant_id or self._get_tenant_id()

        # These variables shouldn't be changed manually
        self.site_name: str = parsed_site_url.path.split(
            self.site_separator, maxsplit=1
        )[-1]

        if token_provider is not None:
            self.ca = None
        elif isinstance(credentials, ClientCredential):
            self.ca = ConfidentialClientApplication(
                client_id=credentials.client_id,
                client_credential=credentials.client_secret,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            )
        else:
            warnings.warn(
                "UserDelegatedCredential is deprecated; use an injected delegated token provider",
                DeprecationWarning,
                stacklevel=2,
            )
            self.ca = PublicClientApplication(
                client_id=credentials.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            )

        self.document_folder_name: str | None = document_folder_name

        self._site_id: str = self._get_site_id()
        self._drive_id: str = self._get_drive_id()
        # ``document_folder_name`` may have been resolved by ``_get_drive_id`` when
        # it was initially ``None`` (auto-detected default document library).
        self.relative_path_root: str = (
            f"{self.site_separator}{self.site_name}/{self.document_folder_name}"
        )
        self.folder: SPFolder = self._get_folder("")

    # ----------------------------------------------------------
    # Lifecycle / debugging helpers
    # ----------------------------------------------------------

    def __repr__(self) -> str:
        return f"SharepointManager(site={self.url!r}, document_folder={self.document_folder_name!r})"

    def close(self) -> None:
        """Release the underlying HTTP session."""
        provider = getattr(self, "_token_provider", None)
        if provider is not None and hasattr(provider, "close"):
            provider.close()
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> "SharepointManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def cwd(self, sp_relative_folder_path: str, create_folder: bool = False):
        """
        Temporarily switch ``self.folder`` to ``sp_relative_folder_path``.

        Restores the previous folder on exit, even on exceptions, so calls
        nested inside the ``with`` block don't leak state to the caller.
        """
        previous = self.folder
        try:
            self.set_folder(sp_relative_folder_path, create_folder=create_folder)
            yield self.folder
        finally:
            self.folder = previous

    # ----------------------------------------------------------
    # Direct URL methods (share URL)
    # ----------------------------------------------------------

    def get_file_metadata_from_url(self, url: str) -> SPFile:
        """
        Retrieve file metadata from a SharePoint file URL.


        Parameters
        ----------
        url : str
            The SharePoint file URL.


        Returns
        -------
        SPFile
            The file metadata.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> file_metadata = manager.get_file_metadata_from_url(url = "https://tenant.sharepoint.com/...")
        """

        self._validate_sharepoint_url(url)
        base64_url = base64.b64encode(url.encode("utf-8")).decode("utf-8")
        encoded_url = "u!" + base64_url.rstrip("=").replace("/", "_").replace("+", "-")

        graph_url = f"{self._graph_base_url}/shares/{encoded_url}/driveItem"
        headers = self._hdr()

        r = self._request(
            "GET", graph_url, headers=headers, timeout=30, authenticated=True
        )
        r.raise_for_status()
        data = r.json()
        self._validate_item_boundary(data)
        file = SPFile.from_dict(data)

        return file

    def download_file_from_url(
        self,
        url: str,
        local_download_path: str,
        new_filename: str | None = None,
    ) -> SPFile:
        """
        Download a file from SharePoint file URL.


        Parameters
        ----------
        url : str
            The SharePoint file URL.
        local_download_path : str
            Local folder to download into.
        new_filename : str, optional
            If provided, rename the downloaded file.


        Returns
        -------
        SPFile
            The downloaded file metadata.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.download_file(url = "https://tenant.sharepoint.com/...", local_download_path = "./Download_Dir")
        """

        file_obj = self.get_file_metadata_from_url(url)

        local_download_path = os.path.abspath(local_download_path)

        os.makedirs(local_download_path, exist_ok=True)

        file_size_bytes = int(file_obj.size)
        self._check_file_budget(file_size_bytes, local_download_path)
        file_size_mbytes = round(file_size_bytes / (1024 * 1024), 1)
        download_url = file_obj.download_url
        logger.info("Downloading file (%s MB)", file_size_mbytes)

        chunk_size = _DOWNLOAD_CHUNK_SIZE
        downloaded_bytes = 0

        filename = file_obj.name if new_filename is None else new_filename
        target_path = safe_join(local_download_path, filename)
        last_log = 0.0
        with self._request("GET", download_url, stream=True, timeout=(10, 300)) as r:
            r.raise_for_status()
            with open(target_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    _ = f.write(chunk)
                    downloaded_bytes += len(chunk)
                    now = time.monotonic()
                    if (
                        now - last_log >= _PROGRESS_LOG_INTERVAL_SEC
                        or downloaded_bytes >= file_size_bytes
                    ):
                        logger.info(
                            "Downloaded %.1f MiB out of %.1f",
                            downloaded_bytes / (1024 * 1024),
                            file_size_bytes / (1024 * 1024),
                        )
                        last_log = now

        logger.info("Download completed")

        return file_obj

    def upload_file_to_url(self, sharing_url: str, local_file_path: str) -> SPFile:
        """
        Uploads a file to SharePoint using an Upload Session.
        Works for any file size by breaking the file into 320KiB-aligned chunks.
        """
        # 1. Resolve the sharing URL to get Drive and Parent IDs
        file_obj = self.get_file_metadata_from_url(sharing_url)
        drive_id = file_obj.parent_reference["driveId"]
        item_id = file_obj.id

        file_size = os.path.getsize(local_file_path)

        # 2. Create the Upload Session
        session_url = (
            f"{self._graph_base_url}/drives/{drive_id}"
            f"/items/{item_id}/createUploadSession"
        )

        # Optional: Conflict behavior (fail, replace, or rename)
        body = {"item": {"@microsoft.graph.conflictBehavior": "replace"}}

        r = self._request(
            "POST", session_url, headers=self._hdr(json_content=True), json=body
        )
        r.raise_for_status()
        upload_url = r.json()["uploadUrl"]

        # 3. Upload the file in chunks
        # Chunk size must be a multiple of 327,680 bytes (320 KiB)
        chunk_size = _UPLOAD_CHUNK_SIZE
        last_log = 0.0
        resp: requests.Response | None = None

        try:
            with open(local_file_path, "rb") as f:
                start = 0
                while start < file_size:
                    chunk = f.read(chunk_size)
                    curr_chunk_len = len(chunk)
                    end = start + curr_chunk_len - 1

                    headers = {
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Length": str(curr_chunk_len),
                    }

                    resp = self._request(
                        "PUT", upload_url, headers=headers, data=chunk, timeout=60
                    )

                    if resp.status_code not in (200, 201, 202):
                        resp.raise_for_status()

                    start = end + 1
                    now = time.monotonic()
                    if (
                        now - last_log >= _PROGRESS_LOG_INTERVAL_SEC
                        or start >= file_size
                    ):
                        logger.info("Uploaded %s/%s bytes...", start, file_size)
                        last_log = now
        except Exception:
            # Best-effort cancel of the upload session if anything goes wrong.
            try:
                self._request("DELETE", upload_url, timeout=30)
            except Exception:
                pass
            raise

        logger.info("Upload complete.")
        if resp is None:
            raise RuntimeError("Upload session produced no response (empty file?)")
        return SPFile.from_dict(resp.json())

    def _consume_delta(
        self, start_url: str
    ) -> tuple[str | None, list["SPFile"], list["SPFolder"], list[SPDeletedItem]]:
        """Iterate a Microsoft Graph delta endpoint and return the latest delta
        link together with the files and folders it yields."""

        next_url: str | None = start_url
        seen: set[str] = set()
        files: list[SPFile] = []
        folders: list[SPFolder] = []
        deleted: list[SPDeletedItem] = []
        latest_delta_link: str | None = None
        page_count = 0

        while next_url:
            if next_url in seen:
                raise SPValidationError("Repeated Graph delta link")
            seen.add(next_url)
            self._validate_graph_url(next_url)
            response = self._request(
                "GET", next_url, headers=self._hdr(), timeout=30, authenticated=True
            )
            response.raise_for_status()

            payload = response.json()
            for item in payload.get("value", []):
                if "deleted" in item:
                    deleted.append(SPDeletedItem.from_dict(item))
                elif "file" in item:
                    files.append(SPFile.from_dict(item))
                elif "folder" in item:
                    folders.append(SPFolder.from_dict(item))

            latest_delta_link = payload.get("@odata.deltaLink", latest_delta_link)
            next_url = payload.get("@odata.nextLink")
            page_count += 1

        if latest_delta_link is None and page_count > 0:
            logger.warning(
                "Delta endpoint returned no @odata.deltaLink across %d page(s); "
                "subsequent incremental queries may be impossible.",
                page_count,
            )

        return latest_delta_link, files, folders, deleted

    def get_folder_delta(
        self,
        sp_relative_folder_path: str = "",
        delta_link: str | None = None,
    ) -> tuple[str | None, list[SPFile], list[SPFolder], list[SPDeletedItem]]:
        """
        Return the latest delta link plus the files and folders found beneath a
        folder identified by its path relative to the document library root.

        Parameters
        ----------
        sp_relative_folder_path : str, optional
            Folder path relative to the document library (e.g. ``"Folder/Subfolder"``).
            Defaults to ``""`` which resolves to the document library root.
        delta_link : str | None, optional
            Existing Microsoft Graph delta link. If provided, only changes are fetched
            and ``sp_relative_folder_path`` is ignored.

        Returns
        -------
        tuple[str | None, list[SPFile], list[SPFolder], list[SPDeletedItem]]
            The latest delta link, changed files, changed folders, and tombstones.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> delta_link, files, folders = manager.get_folder_delta("Folder/Subfolder")
        """

        if delta_link is not None:
            return self._consume_delta(delta_link)

        folder = self._get_folder(sp_relative_folder_path)
        start_url = (
            f"{self._graph_base_url}/drives/{self._drive_id}/items/{folder.id}/delta"
        )
        return self._consume_delta(start_url)

    def get_folder_delta_from_url(
        self,
        url: str,
        delta_link: str | None = None,
    ) -> tuple[str | None, list[SPFile], list[SPFolder], list[SPDeletedItem]]:
        """
        Return the latest delta link plus the files and folders found beneath a
        folder identified by its absolute SharePoint URL.

        Parameters
        ----------
        url : str
            Absolute SharePoint folder URL.
        delta_link : str | None, optional
            Existing Microsoft Graph delta link. If provided, only changes are fetched
            and ``url`` is ignored.

        Returns
        -------
        tuple[str | None, list[SPFile], list[SPFolder]]
            The latest delta link, the list of files and the list of folders.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> delta_link, files, folders = manager.get_folder_delta_from_url(
        ...     "https://tenant.sharepoint.com/sites/site/Shared%20Documents/Folder"
        ... )
        """

        if delta_link is not None:
            return self._consume_delta(delta_link)

        self._validate_sharepoint_url(url)
        base64_url = base64.b64encode(url.encode("utf-8")).decode("utf-8")
        encoded_url = "u!" + base64_url.rstrip("=").replace("/", "_").replace("+", "-")

        graph_url = f"{self._graph_base_url}/shares/{encoded_url}/driveItem"
        response = self._request(
            "GET", graph_url, headers=self._hdr(), timeout=30, authenticated=True
        )
        response.raise_for_status()

        folder_item = response.json()
        self._validate_item_boundary(folder_item)
        parent_reference = folder_item.get("parentReference", {})
        drive_id = parent_reference.get("driveId")
        item_id = folder_item.get("id")

        if not drive_id or not item_id:
            raise RuntimeError("Folder metadata is missing driveId or id")

        start_url = f"{self._graph_base_url}/drives/{drive_id}/items/{item_id}/delta"
        return self._consume_delta(start_url)

    # ----------------------------------------------------------
    # Support Methods
    # ----------------------------------------------------------

    def _get_site_id(self) -> str:
        host = urlparse(self.url).hostname
        if not host:
            raise ValueError(f"Could not parse host from URL: {self.url!r}")
        site = self.site_name
        site = "/".join(unquote(part) for part in site.split("/"))
        site = f"{self.site_separator}{site}"
        # Quote the site path while preserving slashes.
        url = f"{self._graph_base_url}/sites/{host}:{quote_path(site)}"
        r = self._request("GET", url, headers=self._hdr(), timeout=30)
        r.raise_for_status()
        self._site_id = r.json()["id"]
        return self._site_id

    def _get_drive_id(self) -> str:
        site_id = self._site_id
        if self.document_folder_name is not None:
            for d in self._paginate(f"{self._graph_base_url}/sites/{site_id}/drives"):
                if d.get("name") == self.document_folder_name:
                    self._drive_id = d["id"]
                    return self._drive_id
            raise SPDriveNotFound("Requested document library was not found")

        # Auto-detect the default document library only when no name was provided.
        r = self._request(
            "GET",
            f"{self._graph_base_url}/sites/{site_id}/drive",
            headers=self._hdr(),
            timeout=30,
        )
        if r.status_code == 200:
            self._drive_id = r.json()["id"]
            self.document_folder_name = unquote(r.json()["webUrl"].split("/")[-1])
            return self._drive_id
        raise RuntimeError("Drive not found for site")

    def _get_folder(self, folder_path: str) -> SPFolder:
        """Resolve a folder under the current drive by its relative path."""

        site_id = self._site_id
        drive_id = self._drive_id
        if folder_path != "":
            site = f":/{quote_path(folder_path.strip('/'))}"
        else:
            site = ""
        r = self._request(
            "GET",
            f"{self._graph_base_url}/sites/{site_id}/drives/{drive_id}/root{site}",
            headers=self._hdr(),
            timeout=30,
        )

        if r.status_code == 404:
            raise SPFolderNotFound("SP Folder not found")
        r.raise_for_status()
        return SPFolder.from_dict(r.json())

    def _get_file(self, filename: str, folder: SPFolder | None = None) -> SPFile:
        """Fetch a file directly under the current folder by name (O(1) lookup)."""
        drive_id = self._drive_id
        folder_id = str((folder or self.folder).id)
        encoded_name = quote_segment(filename)
        url = f"{self._graph_base_url}/drives/{drive_id}/items/{folder_id}:/{encoded_name}"
        r = self._request("GET", url, headers=self._hdr(), timeout=30)
        if r.status_code == 404:
            raise SPFileNotFound("SP file not found")
        r.raise_for_status()
        data = r.json()
        if "file" not in data:
            # Path resolved to a folder, not a file.
            raise SPFileNotFound("SP file not found")
        return SPFile.from_dict(data)

    def _create_single_folder(self, parent_id: str, folder_name: str) -> SPFolder:
        """Create a single child folder under ``parent_id``.

        Retried independently so deep recursion in :meth:`_create_folder`
        does not produce O(attempts**depth) requests.
        """
        drive_id = self._drive_id
        payload = {"name": folder_name, "folder": {}}
        r = self._request(
            "POST",
            f"{self._graph_base_url}/drives/{drive_id}/items/{parent_id}/children",
            headers=self._hdr(json_content=True),
            timeout=30,
            json=payload,
        )
        r.raise_for_status()
        return SPFolder.from_dict(r.json())

    def _create_folder(self, folder_path: str) -> SPFolder | None:
        try:
            return self._get_folder(folder_path)
        except SPFolderNotFound:
            pass

        parts = folder_path.split("/")
        parent_folder = "/".join(parts[:-1])
        folder_name = parts[-1]
        try:
            parent_data = self._get_folder(parent_folder)
        except SPFolderNotFound:
            parent_data = self._create_folder(parent_folder)

        if parent_data is None:
            raise SPFolderNotFound("SP Parent folder not found")

        return self._create_single_folder(parent_data.id, folder_name)

    # ----------------------------------------------------------
    # Basic file system functions
    # ---------------------------------------------------------

    def validate_resource_scope(
        self, site_id: str | None = None, drive_id: str | None = None
    ) -> bool:
        """Validate that an optional deployment grant matches this manager."""
        if site_id is not None and site_id != self._site_id:
            raise SPUnauthorizedTarget(
                "Configured site does not match the selected resource grant"
            )
        if drive_id is not None and drive_id != self._drive_id:
            raise SPUnauthorizedTarget(
                "Configured drive does not match the selected resource grant"
            )
        return True

    def _check_file_budget(self, size: int, destination: str | None = None) -> None:
        if (
            size < 0
            or size > self.policy.max_file_bytes
            or size > self.policy.max_total_bytes
        ):
            raise SPValidationError("File exceeds the configured transfer budget")
        if destination is not None:
            parent = os.path.dirname(os.path.abspath(destination)) or "."
            if (
                size > self.policy.max_disk_bytes
                or shutil.disk_usage(parent).free < size
            ):
                raise SPValidationError("Insufficient configured disk budget")

    def _check_depth(self, path: str | None) -> None:
        depth = len(get_names_to_folder(path or ""))
        if depth > self.policy.max_depth:
            raise SPValidationError(
                "Folder depth exceeds the configured traversal budget"
            )

    def get_file_author(self, file: SPFile) -> dict[str, dict[str, str]]:
        """
        Return author and editor metadata for a SharePoint file.


        Parameters
        ----------
        file : SPFile
            File object.


        Returns
        -------
        dict
            Dictionary with "author" and "editor" entries.
        """

        def _extract(by: dict[str, Any] | None) -> dict[str, str]:
            # Graph's ``createdBy`` / ``lastModifiedBy`` look like
            # ``{"user": {...}, "application": {...}}``. Prefer the user
            # identity; fall back to whatever identity is present.
            by = by or {}
            identity = by.get("user")
            if not isinstance(identity, dict):
                identity = next((v for v in by.values() if isinstance(v, dict)), {})
            return {
                "id": str(identity.get("id", "")),
                "display_name": str(identity.get("displayName", "")),
                "email": str(identity.get("email", "")),
            }

        return {
            "author": _extract(file.created_by),
            "editor": _extract(file.last_modified_by),
        }

    def _resolve_folder(
        self, sp_relative_folder_path: str, create_folder: bool = False
    ) -> SPFolder:
        fnames = get_names_to_folder(sp_relative_folder_path)
        if not fnames:
            return self._get_folder("")
        target_folder = "/".join(fnames)
        try:
            folder_data = self._get_folder(target_folder)
        except SPFolderNotFound:
            if not create_folder:
                raise SPFolderNotFound("SP Folder does not exist")
            folder_data = self._create_folder(target_folder)
        if folder_data is None:
            raise SPFolderNotFound("SP Folder could not be created or resolved")
        if folder_data.name != fnames[-1]:
            raise RuntimeError("SP Folder was not resolved correctly")
        return folder_data

    def set_folder(
        self, sp_relative_folder_path: str, create_folder: bool = False
    ) -> SPFolder:
        """
        Set the current working folder.


        Parameters
        ----------
        sp_relative_folder_path : str
            Relative path within the document library.
        create_folder : bool, optional
            If True, create the folder (and ancestors) if it does not exist.


        Returns
        -------
        SPFolder
            The set folder object.


        Raises
        ------
        SPFolderNotFound
            If the folder does not exist and `create_folder` is False.


        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> try:
        >>>     manager.set_folder(sp_relative_folder_path = "Folder1/Folder2/Folder3", create_folder = False)
        >>> except SPFolderNotFound:
        >>>     logging.info("Folder does not exist inside Sharepoint!")
        >>> manager.set_folder(sp_relative_folder_path = "Folder1/Folder2/Folder3", create_folder = True) # Creates folder
        """

        self.folder = self._resolve_folder(sp_relative_folder_path, create_folder)
        return self.folder

    def _list_children(
        self, folder: SPFolder | None = None
    ) -> tuple[dict[str, SPFile], dict[str, SPFolder]]:
        """Single-pass enumeration of a folder's children.

        Returns ``(files_by_name, folders_by_name)``. Saves a network round
        trip versus calling :meth:`list_files` and :meth:`list_folders`.
        """
        target = folder if folder is not None else self.folder
        drive_id = self._drive_id
        url = f"{self._graph_base_url}/drives/{drive_id}/items/{target.id}/children"
        files: dict[str, SPFile] = {}
        folders: dict[str, SPFolder] = {}
        for item in self._paginate(url):
            if "file" in item:
                f = SPFile.from_dict(item)
                files[f.name] = f
            elif "folder" in item:
                fd = SPFolder.from_dict(item)
                folders[fd.name] = fd
        return files, folders

    def list_files(
        self, sp_relative_folder_path: str | None = None
    ) -> dict[str, SPFile]:
        """
        List files in a SharePoint folder.


        Parameters
        ----------
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Returns
        -------
        dict
            Mapping of filename to SPFile objects.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> files = manager.list_files(sp_relative_folder_path = "Folder1/Folder2/Folder3") # Changes self.folder and lists the files
        """

        target = (
            self._resolve_folder(sp_relative_folder_path)
            if sp_relative_folder_path is not None
            else self.folder
        )
        files, _ = self._list_children(target)
        return files

    def list_folders(
        self, sp_relative_folder_path: str | None = None
    ) -> dict[str, SPFolder]:
        """
        List subfolders in a SharePoint folder.


        Parameters
        ----------
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Returns
        -------
        dict
            Mapping of folder name to SPFolder objects.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> folders = manager.list_folders(sp_relative_folder_path = "Folder1/Folder2/Folder3") # Changes self.folder and lists the folders
        """

        target = (
            self._resolve_folder(sp_relative_folder_path)
            if sp_relative_folder_path is not None
            else self.folder
        )
        _, folders = self._list_children(target)
        return folders

    # ----------------------------------------------------------
    # Upload files/folders to Sharepoint
    # ----------------------------------------------------------

    def upload_file(
        self,
        local_file_path: str,
        sp_relative_folder_path: str | None = None,
        _folder: SPFolder | None = None,
    ) -> None:
        """
        Upload a local file to SharePoint.


        Parameters
        ----------
        local_file_path : str
            Path to the local file.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Raises
        ------
        FileNotFoundError
            If the local file does not exist or is not a file.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.upload_file(local_file_path = "file.txt", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_file_path = os.path.abspath(local_file_path)
        if os.path.islink(local_file_path):
            raise SPValidationError("Symlink upload roots are not allowed")
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(f"Local file does not exist: {local_file_path}")
        if not os.path.isfile(local_file_path):
            raise FileNotFoundError(
                f"Path does not correspond to a file: {local_file_path}"
            )

        target_folder = _folder or (
            self._resolve_folder(sp_relative_folder_path, create_folder=True)
            if sp_relative_folder_path is not None
            else self.folder
        )

        file_name = get_filename(local_file_path)
        file_size_b = os.path.getsize(local_file_path)
        self._check_file_budget(file_size_b)
        file_size_mb = file_size_b / (1024 * 1024)

        logger.info("Uploading file (%.1f MB)", file_size_mb)

        with open(local_file_path, "rb") as file:
            site_id = self._site_id
            drive_id = self._drive_id
            folder_id = target_folder.id
            encoded_name = quote_segment(file_name)
            url = (
                f"{self._graph_base_url}/sites/{site_id}/drives/{drive_id}"
                f"/items/{folder_id}:/{encoded_name}:/createUploadSession"
            )
            request_body = {"@microsoft.graph.conflictBehavior": "replace"}
            r = self._request(
                "POST",
                url,
                headers=self._hdr(json_content=True),
                timeout=30,
                json=request_body,
            )
            r.raise_for_status()
            upload_session = r.json()
            upload_url = str(upload_session["uploadUrl"])

            chunk_size = _UPLOAD_CHUNK_SIZE
            start_byte = 0
            last_log = 0.0
            try:
                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break

                    end_byte = start_byte + len(chunk) - 1
                    content_range = f"bytes {start_byte}-{end_byte}/{file_size_b}"

                    chunk_headers = {
                        "Content-Length": str(len(chunk)),
                        "Content-Range": content_range,
                    }

                    response = self._request(
                        "PUT",
                        upload_url,
                        headers=chunk_headers,
                        timeout=60,
                        data=chunk,
                    )
                    response.raise_for_status()

                    start_byte += len(chunk)
                    now = time.monotonic()
                    if (
                        now - last_log >= _PROGRESS_LOG_INTERVAL_SEC
                        or start_byte >= file_size_b
                    ):
                        logger.info(
                            "Uploaded %.1f MiB out of %.1f",
                            start_byte / (1024 * 1024),
                            file_size_b / (1024 * 1024),
                        )
                        last_log = now
            except Exception:
                # Cancel upload session on failure to free server-side state.
                try:
                    _ = self._request("DELETE", upload_url, timeout=30)
                except Exception:
                    pass
                raise

        logger.info("Upload completed.")

    def upload_folder(
        self,
        local_folder_path: str,
        sp_relative_folder_path: str | None = None,
        _folder: SPFolder | None = None,
        _depth: int = 0,
    ) -> None:
        """
        Recursively upload a local folder and its contents to SharePoint.


        Parameters
        ----------
        local_folder_path : str
            Path to the local folder.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Raises
        ------
        FileNotFoundError
            If the local folder does not exist.
        ValueError
            If the path is not a folder.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.upload_folder(local_file_path = "./Folder4", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_folder_path = os.path.abspath(local_folder_path)
        depth = _depth or len(get_names_to_folder(sp_relative_folder_path or ""))
        if depth > self.policy.max_depth:
            raise SPValidationError("Folder depth exceeds the configured traversal budget")
        if os.path.islink(local_folder_path):
            raise SPValidationError("Symlink upload roots are not allowed")
        if not os.path.exists(local_folder_path):
            raise FileNotFoundError(f"Local folder does not exist: {local_folder_path}")
        if not os.path.isdir(local_folder_path):
            raise ValueError(
                f"Path does not correspond to a folder: {local_folder_path}"
            )

        base_folder = _folder or (
            self._resolve_folder(sp_relative_folder_path, create_folder=True)
            if sp_relative_folder_path is not None
            else self.folder
        )
        base_relative = base_folder.relative_url

        new_folder_name = os.path.basename(local_folder_path)
        sp_folder_path = f"{base_relative}/{new_folder_name}".lstrip("/")
        logger.info("Uploading folder")

        with os.scandir(local_folder_path) as entries:
            files: list[str] = []
            subdirs: list[str] = []
            for entry in entries:
                if entry.is_symlink():
                    raise SPValidationError("Symlinks are not allowed in upload trees")
                if entry.is_file(follow_symlinks=False):
                    files.append(entry.path)
                elif entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.path)

        target_folder = self._resolve_folder(sp_folder_path, create_folder=True)
        for file_path in files:
            self.upload_file(file_path, _folder=target_folder)

        for subdir_path in subdirs:
            self.upload_folder(subdir_path, _folder=target_folder, _depth=depth + 1)

    # ----------------------------------------------------------
    # Download files/folders from Sharepoint
    # ----------------------------------------------------------

    def download_file(
        self,
        file: str | SPFile,
        local_download_path: str,
        sp_relative_folder_path: str | None = None,
        new_filename: str | None = None,
        _folder: SPFolder | None = None,
    ) -> SPFile:
        """
        Download a file from SharePoint.


        Parameters
        ----------
        file : str | SPFile
            Filename or SPFile instance.
        local_download_path : str
            Local folder to download into.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.
        new_filename : str, optional
            If provided, rename the downloaded file.


        Returns
        -------
        SPFile
            The downloaded file metadata.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.download_file(filename = "file.txt", local_download_path = "./Download_Dir",
        ...     sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_download_path = os.path.abspath(local_download_path)
        self._check_depth(sp_relative_folder_path)

        os.makedirs(local_download_path, exist_ok=True)

        if isinstance(file, str):
            target_folder = _folder or (
                self._resolve_folder(sp_relative_folder_path)
                if sp_relative_folder_path is not None
                else self.folder
            )
            file_obj = self._get_file(file, target_folder)
        else:
            file_obj = file

        file_size_bytes = int(file_obj.size)
        self._check_file_budget(file_size_bytes, local_download_path)
        file_size_mbytes = round(file_size_bytes / (1024 * 1024), 1)
        download_url = file_obj.download_url
        logger.info("Downloading file (%s MB)", file_size_mbytes)

        chunk_size = _DOWNLOAD_CHUNK_SIZE
        downloaded_bytes = 0

        filename = file_obj.name if new_filename is None else new_filename
        target_path = safe_join(local_download_path, filename)
        last_log = 0.0
        with self._request("GET", download_url, stream=True, timeout=(10, 300)) as r:
            r.raise_for_status()
            with open(target_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    _ = f.write(chunk)
                    downloaded_bytes += len(chunk)
                    now = time.monotonic()
                    if (
                        now - last_log >= _PROGRESS_LOG_INTERVAL_SEC
                        or downloaded_bytes >= file_size_bytes
                    ):
                        logger.info(
                            "Downloaded %.1f MiB out of %.1f",
                            downloaded_bytes / (1024 * 1024),
                            file_size_bytes / (1024 * 1024),
                        )
                        last_log = now

        logger.info("Download completed")

        return file_obj

    def download_folder(
        self,
        local_download_path: str,
        sp_relative_folder_path: str | None = None,
        _folder: SPFolder | None = None,
        _depth: int = 0,
    ) -> None:
        """
        Recursively download a SharePoint folder and its contents.


        Parameters
        ----------
        local_download_path : str
            Local destination path.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.

        Returns
        -------
        None

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> # The code below will create a folder "Folder3" inside "./Download_Dir"
        >>> manager.download_folder(local_download_path = "./Download_Dir",
        ...     sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_download_path = os.path.abspath(local_download_path)
        depth = _depth or len(get_names_to_folder(sp_relative_folder_path or ""))
        if depth > self.policy.max_depth:
            raise SPValidationError("Folder depth exceeds the configured traversal budget")

        # Create local folder
        cur_folder = _folder or (
            self._resolve_folder(sp_relative_folder_path)
            if sp_relative_folder_path is not None
            else self.folder
        )
        logger.info("Downloading folder")
        cur_folder_download_path = (
            safe_join(local_download_path, cur_folder.name)
            if cur_folder.name
            else local_download_path
        )
        os.makedirs(cur_folder_download_path, exist_ok=True)

        # Single-pass enumeration to halve Graph round-trips per folder.
        files, subfolders = self._list_children(cur_folder)

        for file in files.values():
            _ = self.download_file(file, cur_folder_download_path)

        # Recurse using the resolved subfolder paths.
        for subfolder in subfolders.values():
            self.download_folder(
                cur_folder_download_path,
                _folder=subfolder,
                _depth=depth + 1,
            )

    def delete_file(
        self, file: str | SPFile, sp_relative_folder_path: str | None = None
    ) -> None:
        """
        Delete a file from SharePoint.


        Parameters
        ----------
        file : str | SPFile
            Filename or SPFile instance.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Returns
        -------
        None


        Raises
        ------
        SPFileNotFound
            If the file does not exist.


        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.delete_file(filename = "file.txt", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        if isinstance(file, str):
            target_folder = (
                self._resolve_folder(sp_relative_folder_path)
                if sp_relative_folder_path is not None
                else self.folder
            )
            file = self._get_file(file, target_folder)
        else:
            self._validate_file_boundary(file)

        drive_id = self._drive_id
        item_id = file.id
        r = self._request(
            "DELETE",
            f"{self._graph_base_url}/drives/{drive_id}/items/{item_id}",
            headers=self._hdr(),
            timeout=30,
        )
        r.raise_for_status()

    def delete_folder(
        self,
        folder: str | SPFolder,
        force_delete: bool = False,
        sp_relative_folder_path: str | None = None,
    ) -> None:
        """
        Delete a SharePoint folder.


        Parameters
        ----------
        folder : str | SPFolder
            Relative path or folder object.
        force_delete : bool, optional
            If False (default), only empty folders are deleted. If True, delete regardless.
        sp_relative_folder_path : str, optional
            Relative path within the document library to scope the operation.
            If provided, ``set_folder`` is invoked first; ``folder`` is then
            interpreted relative to that location (when given as a ``str``).


        Raises
        ------
        SPFolderNotEmpty
            If the folder is not empty and `force_delete` is False.


        Returns
        -------
        None


        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> # Consider that the folder is not empty
        >>> try:
        >>>     manager.delete_folder("Folder1/Folder2/Folder3", force_delete=False)
        >>> except SPFolderNotEmpty:
        >>>     logging.info("Sharepoint folder is not empty")
        >>> manager.delete_folder("Folder1/Folder2/Folder3", force_delete=True)
        """

        # Resolve the target folder without leaking ``self.folder`` mutation.
        if isinstance(folder, str):
            scope_path = (
                f"{sp_relative_folder_path.rstrip('/')}/{folder.lstrip('/')}"
                if sp_relative_folder_path
                else folder
            )
            target = self._resolve_folder(scope_path)
        else:
            target = folder
            self._validate_object_boundary(target)

        files, folders = self._list_children(target)

        if (len(files) == 0 and len(folders) == 0) or force_delete:
            drive_id = self._drive_id
            folder_id = target.id
            r = self._request(
                "DELETE",
                f"{self._graph_base_url}/drives/{drive_id}/items/{folder_id}",
                headers=self._hdr(),
                timeout=30,
            )
            r.raise_for_status()
        else:
            raise SPFolderNotEmpty("Sharepoint folder not empty")
