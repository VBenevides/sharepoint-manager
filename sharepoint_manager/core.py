"""
Module used to interact with sharepoint sites using an approach similar to file systems
"""

# ---------------------------------------------------------------------- #
# Imports
# ---------------------------------------------------------------------- #

import logging
import os
import re
import shutil
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from email.utils import parsedate_to_datetime
from typing import Any, BinaryIO, Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4

import requests
from msal import ConfidentialClientApplication, PublicClientApplication

from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPCollectionPage,
    SPDeletedItem,
    SPDeltaPage,
    SPFile,
    SPFolder,
    TokenProvider,
    UserDelegatedCredential,
)
from .exceptions import (
    SPAmbiguousWriteError,
    SPAuthenticationError,
    SPAuthorizationError,
    SPConflictError,
    SPDeadlineExceeded,
    SPDriveNotFound,
    SPFileIntegrityError,
    SPFileNotFound,
    SPFolderNotEmpty,
    SPFolderNotFound,
    SPGraphError,
    SPNotFoundError,
    SPThrottledError,
    SPUnauthorizedTarget,
    SPValidationError,
)
from .urls import (
    GRAPH_HOSTS,
    safe_graph_error_detail,
    share_id,
    sharepoint_location_path,
    validate_capability_url,
    validate_graph_url,
    validate_sharepoint_url,
)
from .utils import (
    QuickXorHash,
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
# One direct Graph content request is cheaper than creating a resumable session
# for files that fit in the normal upload chunk.
_DIRECT_UPLOAD_MAX_BYTES = _UPLOAD_CHUNK_SIZE
# Streaming download chunk size.
_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024
# GUID pattern used to extract a tenant id from authorization URIs.
_GUID_RE = re.compile(r"/([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})")


class SharepointManagerBase:
    """
    Base class for Sharepoint Manager Classes
    """

    credentials: ClientCredential | UserDelegatedCredential
    ca: ConfidentialClientApplication | PublicClientApplication
    _session: requests.Session
    tenant_url: str

    # Per-instance locks guard the token cache and HTTP session.
    _token_lock: threading.Lock

    def _emit_telemetry(self, event: str, **fields: Any) -> None:
        correlation_id = getattr(self, "_correlation_id", None)
        if not correlation_id:
            correlation_id = uuid4().hex
            self._correlation_id = correlation_id
        record = {
            "event": event,
            "correlation_id": correlation_id,
            "operation": fields.pop("operation", event.rsplit(".", 1)[-1]),
            "elapsed_ms": fields.pop("elapsed_ms", 0.0),
            "status": fields.pop("status", None),
            **fields,
        }
        failed = (
            bool(record.get("failure_class"))
            or record.get("outcome") == "failure"
            or (isinstance(record["status"], int) and record["status"] >= 400)
        )
        logger.log(
            logging.ERROR if failed else logging.INFO,
            "sharepoint event",
            extra={"sharepoint_event": record},
        )
        callback = getattr(self, "telemetry", None)
        if not callable(callback):
            return
        try:
            # ponytail: telemetry is best-effort; callback health must not affect transfers.
            callback(record)
        except Exception:
            logger.debug("Telemetry callback failed", exc_info=True)

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
        validate_graph_url(url, self.graph_host)

    def _validate_capability_url(self, url: str) -> None:
        validate_capability_url(url, self.graph_host)

    @staticmethod
    def _validate_sharepoint_url(url: str) -> Any:
        return validate_sharepoint_url(url)

    def _validate_item_boundary(self, item: dict[str, Any]) -> None:
        parent = item.get("parentReference")
        if not isinstance(parent, dict):
            raise SPUnauthorizedTarget("Resolved item has no trusted parent reference")
        drive_id = parent.get("driveId")
        site_id = parent.get("siteId")
        configured_site_id = str(self._site_id)
        configured_site_ids = {configured_site_id}
        if "," in configured_site_id:
            configured_site_ids.update(configured_site_id.split(",")[1:])
        if drive_id != self._drive_id or (
            site_id is not None and site_id not in configured_site_ids
        ):
            raise SPUnauthorizedTarget(
                "Resolved item is outside the configured SharePoint boundary"
            )

    def _validate_object_boundary(self, obj: SPFile | SPFolder) -> None:
        drive_id = obj.parent_reference.get("driveId")
        site_id = obj.parent_reference.get("siteId")
        if drive_id != self._drive_id or (
            site_id is not None and site_id != self._site_id
        ):
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
        with self._token_lock:
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
                scopes = [f"https://{self.graph_host}/.default"]
                result = None
                if getattr(self, "_account", None) is None:
                    accounts = self.ca.get_accounts(username=self._username)
                    self._account = accounts[0] if accounts else None
                if self._account is not None:
                    result = self.ca.acquire_token_silent(
                        scopes=scopes, account=self._account
                    )
                    if isinstance(result, dict) and "access_token" in result:
                        self._password = None
                        self.credentials = None
                    else:
                        result = None
                if not isinstance(result, dict) or "access_token" not in result:
                    if not self._warned_password_auth:
                        warnings.warn(
                            "Username/password authentication is deprecated and not recommended; "
                            "ROPC does not support MFA or Conditional Access, and the password "
                            "is used only for initial token bootstrap.",
                            UserWarning,
                            stacklevel=2,
                        )
                        self._warned_password_auth = True
                    if self._password is None:
                        raise SPAuthenticationError(
                            "Silent authentication failed; credentials must be supplied again"
                        )
                    result = self.ca.acquire_token_by_username_password(
                        username=self._username,
                        password=self._password,
                        scopes=scopes,
                    )
                    if isinstance(result, dict) and "access_token" in result:
                        self._password = None
                        self.credentials = None
                        accounts = self.ca.get_accounts(username=self._username)
                        self._account = accounts[0] if accounts else None
            else:
                result = self.ca.acquire_token_for_client(
                    scopes=[f"https://{self.graph_host}/.default"]
                )

            if not isinstance(result, dict) or "access_token" not in result:
                raise SPAuthenticationError("Authentication failed")

            token = str(result["access_token"])
            # Prefer 'expires_on' (epoch seconds as str) else compute from 'expires_in'
            try:
                expires_on = int(result.get("expires_on", 0))
            except (TypeError, ValueError):
                expires_on = 0
            if not expires_on:
                try:
                    expires_in = int(result.get("expires_in", 3600))
                except (TypeError, ValueError):
                    expires_in = 3600
                expires_on = now + max(expires_in, 60)

            # Cache the token and its expiry
            self._cached_token = token
            self._cached_token_expiry = int(expires_on)
            self._emit_telemetry("auth.token_refresh", success=True)

            return token

    # ----------------------------------------------------------
    # Internal HTTP helpers
    # ----------------------------------------------------------

    def _request_gate_for(self) -> threading.BoundedSemaphore:
        gate = getattr(self, "_request_gate", None)
        if gate is None:
            gate = threading.BoundedSemaphore(
                getattr(getattr(self, "policy", None), "max_concurrency", 1)
            )
            self._request_gate = gate
        return gate

    def _request_condition_for(self) -> threading.Condition:
        condition = getattr(self, "_request_condition", None)
        if condition is None:
            condition = threading.Condition()
            self._request_condition = condition
            self._active_requests = 0
        return condition

    def _request_session(self) -> requests.Session:
        if not getattr(self, "_owns_session", False):
            return self._session
        local = getattr(self, "_session_local", None)
        if local is None:
            local = threading.local()
            self._session_local = local
        session = getattr(local, "session", None)
        if session is None:
            if threading.get_ident() == getattr(self, "_owner_thread_id", None):
                session = self._session
            else:
                session = requests.Session()
            local.session = session
            registry_lock = getattr(self, "_session_registry_lock", None)
            if registry_lock is None:
                registry_lock = threading.Lock()
                self._session_registry_lock = registry_lock
            with registry_lock:
                self._session_registry[threading.get_ident()] = session
        return session

    def _perform_request(self, **kwargs: Any) -> requests.Response:
        with self._request_gate_for():
            condition = self._request_condition_for()
            with condition:
                if getattr(self, "_closed", False):
                    raise SPValidationError("SharePoint manager is closed")
                self._active_requests += 1
            try:
                session = self._request_session()
                if not getattr(self, "_owns_session", False):
                    shared_lock = getattr(self, "_shared_session_lock", None)
                    if shared_lock is None:
                        shared_lock = threading.Lock()
                        self._shared_session_lock = shared_lock
                    with shared_lock:
                        return session.request(**kwargs)
                return session.request(**kwargs)
            finally:
                with condition:
                    self._active_requests -= 1
                    if self._active_requests == 0:
                        condition.notify_all()

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
        if getattr(self, "_closed", False):
            raise SPValidationError("SharePoint manager is closed")
        if authenticated is None:
            authenticated = bool(
                headers and str(headers.get("Authorization", "")).startswith("Bearer ")
            )
        if authenticated:
            self._validate_graph_url(url)
        elif url != self.tenant_url:
            self._validate_capability_url(url)
        if allow_redirects is None:
            allow_redirects = (
                not authenticated
                and getattr(
                    self, "policy", OperationPolicy()
                ).allow_capability_redirects
            )
        method = method.upper()
        retryable = method in _RETRY_METHODS
        attempt = 1
        policy = getattr(self, "policy", OperationPolicy())
        max_attempts = min(max_attempts, policy.max_retry_attempts) if retryable else 1
        deadline = time.monotonic() + policy.wall_clock_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SPDeadlineExceeded(
                    "Graph request deadline exceeded", retryable=True
                )
            request_started = time.monotonic()
            request_timeout = remaining if timeout is None else timeout
            if isinstance(request_timeout, tuple):
                request_timeout = tuple(
                    min(float(value), remaining) for value in request_timeout
                )
            elif isinstance(request_timeout, (int, float)):
                request_timeout = min(float(request_timeout), remaining)
            try:
                resp = self._perform_request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=request_timeout,
                    json=json,
                    data=data,
                    params=params,
                    stream=stream,
                    allow_redirects=allow_redirects,
                )
            except requests.RequestException as exc:
                self._emit_telemetry(
                    "graph.request",
                    method=method,
                    attempt=attempt,
                    elapsed_ms=round((time.monotonic() - request_started) * 1000, 1),
                    failure_class=type(exc).__name__,
                    retryable=retryable,
                )
                if (
                    not retryable
                    or attempt >= max_attempts
                    or time.monotonic() >= deadline
                ):
                    if time.monotonic() >= deadline:
                        if not authenticated:
                            raise SPDeadlineExceeded(
                                "Graph request deadline exceeded", retryable=True
                            ) from None
                        raise SPDeadlineExceeded(
                            "Graph request deadline exceeded",
                            retryable=True,
                            cause=exc,
                        ) from exc
                    if not authenticated:
                        raise SPGraphError("Capability request failed") from None
                    raise
                time.sleep(min(2**attempt, policy.max_retry_after_seconds))
                attempt += 1
                continue
            if time.monotonic() >= deadline:
                request_id = resp.headers.get("request-id") or resp.headers.get(
                    "client-request-id"
                )
                status = resp.status_code
                resp.close()
                raise SPDeadlineExceeded(
                    "Graph request deadline exceeded",
                    status=status,
                    request_id=request_id,
                    retryable=True,
                )
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
            self._emit_telemetry(
                "graph.request",
                method=method,
                attempt=attempt,
                status=resp.status_code,
                request_id=request_id,
                elapsed_ms=round((time.monotonic() - request_started) * 1000, 1),
                throttled=resp.status_code == 429,
                retrying=resp.status_code in _RETRY_STATUSES and attempt < max_attempts,
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
                except Exception:  # noqa: BLE001, S110
                    pass
                if time.monotonic() >= deadline:
                    raise SPDeadlineExceeded(
                        "Graph request deadline exceeded",
                        status=resp.status_code,
                        request_id=request_id,
                        retryable=True,
                    )
                time.sleep(delay)
                attempt += 1
                continue
            return resp

    def _raise_for_status(
        self,
        response: requests.Response,
        *,
        not_found: type[SPNotFoundError] = SPNotFoundError,
    ) -> None:
        status = int(getattr(response, "status_code", 0))
        if status < 400:
            return
        headers = getattr(response, "headers", {})
        request_id = headers.get("request-id") or headers.get("client-request-id")
        try:
            response.raise_for_status()
        except requests.RequestException as cause:
            error = cause
        else:
            error = None
        error_type = {
            404: not_found,
            401: SPAuthorizationError,
            403: SPAuthorizationError,
            409: SPConflictError,
            429: SPThrottledError,
        }.get(status, SPGraphError)
        self._emit_telemetry(
            "graph.error",
            operation="request",
            status=status,
            request_id=request_id,
            failure_class=error_type.__name__,
            retryable=status in _RETRY_STATUSES,
        )
        message = "Graph request failed"
        detail = safe_graph_error_detail(response)
        if detail:
            message = f"{message}: {detail}"
        raise error_type(
            message,
            status=status,
            request_id=request_id,
            retryable=status in _RETRY_STATUSES,
            cause=error,
        )

    def _paginate(
        self, url: str, _budget: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield items across Graph pages following @odata.nextLink."""
        next_url: str | None = url
        seen: set[str] = set()
        page_count = 0
        item_count = 0
        started = time.monotonic()
        while next_url:
            policy = getattr(self, "policy", OperationPolicy())
            if time.monotonic() - started > policy.wall_clock_seconds:
                raise SPDeadlineExceeded(
                    "Graph pagination deadline exceeded", retryable=True
                )
            if page_count >= policy.max_pages:
                raise SPValidationError("Graph page budget exceeded")
            if _budget is not None:
                self._consume_operation_budget(_budget, pages=1)
            if next_url in seen:
                raise SPValidationError("Repeated Graph pagination link")
            seen.add(next_url)
            self._validate_graph_url(next_url)
            r = self._request(
                "GET", next_url, headers=self._hdr(), timeout=30, authenticated=True
            )
            self._raise_for_status(r)
            try:
                data = r.json()
            finally:
                r.close()
            page_count += 1
            page_items = len(data.get("value", []))
            self._emit_telemetry(
                "graph.page",
                operation="collection",
                page=page_count,
                items=page_items,
                total_items=item_count + page_items,
            )
            for item in data.get("value", []):
                item_count += 1
                if item_count > policy.max_items:
                    raise SPValidationError("Graph item budget exceeded")
                yield item
            next_url = data.get("@odata.nextLink")

    def iter_collection(self, url: str) -> Iterator[SPCollectionPage]:
        """Yield bounded, caller-consumable Graph collection pages."""
        next_url: str | None = url
        seen: set[str] = set()
        page_count = 0
        item_count = 0
        started = time.monotonic()
        while next_url:
            policy = getattr(self, "policy", OperationPolicy())
            if time.monotonic() - started > policy.wall_clock_seconds:
                raise SPDeadlineExceeded(
                    "Graph pagination deadline exceeded", retryable=True
                )
            if page_count >= policy.max_pages:
                raise SPValidationError("Graph page budget exceeded")
            if next_url in seen:
                raise SPValidationError("Repeated Graph pagination link")
            seen.add(next_url)
            self._validate_graph_url(next_url)
            response = self._request(
                "GET", next_url, headers=self._hdr(), timeout=30, authenticated=True
            )
            try:
                self._raise_for_status(response)
                payload = response.json()
            finally:
                response.close()
            values = tuple(payload.get("value", []))
            item_count += len(values)
            if item_count > policy.max_items:
                raise SPValidationError("Graph item budget exceeded")
            page_count += 1
            self._emit_telemetry(
                "graph.page",
                operation="collection",
                page=page_count,
                items=len(values),
                total_items=item_count,
            )
            next_url = payload.get("@odata.nextLink")
            yield SPCollectionPage(values, next_url)

    def iter_folder_delta(
        self, sp_relative_folder_path: str = "", delta_link: str | None = None
    ) -> Iterator[SPDeltaPage]:
        """Yield delta changes page by page and leave checkpoint persistence to the caller."""
        if delta_link is None:
            folder = self._get_folder(sp_relative_folder_path)
            next_url: str | None = (
                f"{self._graph_base_url}/drives/{self._drive_id}/items/{folder.id}/delta"
            )
        else:
            next_url = delta_link
        seen: set[str] = set()
        page_count = 0
        item_count = 0
        started = time.monotonic()
        while next_url:
            policy = getattr(self, "policy", OperationPolicy())
            if time.monotonic() - started > policy.wall_clock_seconds:
                raise SPValidationError("Delta deadline exceeded")
            if page_count >= policy.max_pages or next_url in seen:
                raise SPValidationError("Invalid or repeated delta page")
            seen.add(next_url)
            self._validate_graph_url(next_url)
            response = self._request(
                "GET", next_url, headers=self._hdr(), timeout=30, authenticated=True
            )
            try:
                self._raise_for_status(response)
                payload = response.json()
            finally:
                response.close()
            files = []
            folders = []
            deleted = []
            for item in payload.get("value", []):
                if "deleted" in item:
                    deleted.append(SPDeletedItem.from_dict(item))
                elif "file" in item:
                    files.append(SPFile.from_dict(item))
                elif "folder" in item:
                    folders.append(SPFolder.from_dict(item))
            item_count += len(files) + len(folders) + len(deleted)
            if item_count > policy.max_items:
                raise SPValidationError("Graph item budget exceeded")
            next_page = payload.get("@odata.nextLink")
            checkpoint = payload.get("@odata.deltaLink")
            if checkpoint is not None:
                self._validate_graph_url(checkpoint)
            page_count += 1
            self._emit_telemetry(
                "graph.page",
                operation="delta",
                page=page_count,
                items=len(files) + len(folders) + len(deleted),
                total_items=item_count,
                has_checkpoint=checkpoint is not None,
            )
            next_url = next_page
            yield SPDeltaPage(
                tuple(files), tuple(folders), tuple(deleted), next_page, checkpoint
            )


class SharepointManager(SharepointManagerBase):
    """
    Provides an interface for interacting with a SharePoint site.


    Supports uploading, downloading, listing, and deleting files/folders
    using Microsoft Graph API.


    Examples
    --------
    >>> import os
    >>> creds = ClientCredential(
    ...     os.environ["SP_CLIENT_ID"], os.environ["SP_CLIENT_SECRET"]
    ... )
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
        session: requests.Session | None = None,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """
        Initializes the SharepointManager with a given SharePoint URL and credentials.

        Parameters
        ----------
        sharepoint_site_url : str
            The URL of the SharePoint site. E.g: 'https://{tenant_url}.sharepoint.com/sites/{site_name}'.
        credentials : ClientCredential or UserDelegatedCredential, optional
            Graph API credentials for authentication.
        document_folder_name : str, optional
            The name of the document folder (drive) in the SharePoint site. If ``None``
            (default), the site's default document library is auto-detected via the
            Microsoft Graph ``/drive`` endpoint and its name is resolved from the
            returned ``webUrl``.

        This is vital to guarantee that the class will be able to find the documents in the site.

        graph_host : str, default="graph.microsoft.com"
            Approved Microsoft Graph host.
        tenant_id : str, optional
            Tenant name or GUID. If omitted, it is read from the tenant challenge.
        token_provider : TokenProvider, optional
            Injected token provider used instead of MSAL.
        policy : OperationPolicy, optional
            Transfer budgets and retry limits.
        session : requests.Session, optional
            Injected synchronous HTTP session.
        telemetry : callable, optional
            Best-effort event callback.

        Returns
        -------
        None

        Examples
        --------
        >>> import os
        >>> user_cred = ClientCredential(
        ...     os.environ["SP_CLIENT_ID"], os.environ["SP_CLIENT_SECRET"]
        ... )
        >>> manager = SharepointManager(
        ...     sharepoint_site_url="https://my_tenant.sharepoint.com/sites/my_site",
        ...     credentials=user_cred,
        ... )
        """

        parsed_site_url = self._validate_sharepoint_url(sharepoint_site_url)
        if credentials is None and token_provider is None:
            raise ValueError("credentials or token_provider is required")
        self.graph_host = graph_host.lower().rstrip(".")
        if self.graph_host not in GRAPH_HOSTS:
            raise SPValidationError(
                "graph_host must be an approved Microsoft Graph host"
            )
        self._graph_base_url = f"https://{self.graph_host}/v1.0"
        self.policy = policy or OperationPolicy()
        self._session: requests.Session = (
            session if session is not None else requests.Session()
        )
        self._owns_session = session is None
        self._closed = False
        self._close_lock = threading.Lock()
        self._token_lock = threading.Lock()
        self._request_gate = threading.BoundedSemaphore(self.policy.max_concurrency)
        self._request_condition = threading.Condition()
        self._active_requests = 0
        self._owner_thread_id = threading.get_ident()
        self._session_local = threading.local()
        self._session_local.session = self._session
        self._session_registry_lock = threading.Lock()
        self._session_registry: dict[int, requests.Session] = {
            self._owner_thread_id: self._session
        }
        self._shared_session_lock = threading.Lock()
        self.credentials = credentials
        self._token_provider = token_provider
        self.telemetry = telemetry
        self._correlation_id = uuid4().hex
        self._user_credentials = isinstance(credentials, UserDelegatedCredential)
        self._username = credentials.username if self._user_credentials else None
        self._password = credentials.password if self._user_credentials else None
        self._account = None
        self._warned_password_auth = False

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
        self._root_folder: SPFolder = self._get_folder("")

    # ----------------------------------------------------------
    # Lifecycle / debugging helpers
    # ----------------------------------------------------------

    def __repr__(self) -> str:
        return f"SharepointManager(site={self.url!r}, document_folder={self.document_folder_name!r})"

    def close(self) -> None:
        """Release the underlying HTTP session."""
        condition = self._request_condition_for()
        with condition:
            if getattr(self, "_closed", False):
                return
            self._closed = True
            while self._active_requests:
                condition.wait()
            self._cached_token = ""
            self._cached_token_expiry = 0
            self._password = None
            self._account = None
            self.credentials = None
            provider = getattr(self, "_token_provider", None)
            sessions = ()
            if getattr(self, "_owns_session", True):
                registry_lock = getattr(self, "_session_registry_lock", None)
                if registry_lock is None:
                    sessions = (self._session,)
                else:
                    with registry_lock:
                        sessions = tuple(self._session_registry.values())
                        self._session_registry.clear()
        if provider is not None and hasattr(provider, "close"):
            provider.close()
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def __enter__(self) -> "SharepointManager":  # noqa: PYI034
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----------------------------------------------------------
    # Direct URL methods (share URL)
    # ----------------------------------------------------------

    def _get_drive_item_from_path(
        self, relative_path: str, *, not_found: type[SPNotFoundError]
    ) -> dict[str, Any]:
        endpoint = f"{self._graph_base_url}/drives/{self._drive_id}/root"
        if relative_path:
            endpoint += f":/{quote_path(relative_path)}"
        response = self._request(
            "GET",
            endpoint,
            headers=self._hdr(),
            timeout=30,
            authenticated=True,
        )
        try:
            self._raise_for_status(response, not_found=not_found)
            item = response.json()
        finally:
            response.close()
        self._validate_item_boundary(item)
        return item

    def _get_drive_item_from_url(
        self, url: str, *, not_found: type[SPNotFoundError] = SPNotFoundError
    ) -> dict[str, Any]:
        self._validate_sharepoint_url(url)
        drive_url_name = getattr(self, "_drive_url_name", None) or getattr(
            self, "document_folder_name", ""
        )
        relative_path = sharepoint_location_path(
            url,
            self.url,
            drive_url_name,
        )
        if relative_path is not None:
            return self._get_drive_item_from_path(relative_path, not_found=not_found)
        encoded_url = share_id(url)
        response = self._request(
            "GET",
            f"{self._graph_base_url}/shares/{encoded_url}/driveItem",
            headers=self._hdr(),
            timeout=30,
            authenticated=True,
        )
        try:
            self._raise_for_status(response, not_found=not_found)
            item = response.json()
        finally:
            response.close()
        self._validate_item_boundary(item)
        return item

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

        data = self._get_drive_item_from_url(url, not_found=SPFileNotFound)
        if "file" not in data:
            raise SPFileNotFound("SP file not found")
        return SPFile.from_dict(data)

    def get_folder_metadata_from_url(self, url: str) -> SPFolder:
        """Resolve an approved SharePoint folder URL.

        Parameters
        ----------
        url : str
            Approved SharePoint folder URL.

        Returns
        -------
        SPFolder
            Normalized folder metadata.
        """
        data = self._get_drive_item_from_url(url, not_found=SPFolderNotFound)
        if "folder" not in data and "root" not in data:
            raise SPFolderNotFound("SP folder not found")
        return SPFolder.from_dict(data)

    def list_folder_from_url(
        self, url: str
    ) -> tuple[dict[str, SPFile], dict[str, SPFolder]]:
        """List files and folders below an approved folder URL.

        Parameters
        ----------
        url : str
            Approved SharePoint folder URL.

        Returns
        -------
        tuple[dict[str, SPFile], dict[str, SPFolder]]
            Files and folders keyed by display name.
        """
        return self._list_children(self.get_folder_metadata_from_url(url))

    def create_folder_from_url(self, url: str, name: str) -> SPFolder:
        """Create one child folder below an approved folder URL.

        Parameters
        ----------
        url : str
            Approved SharePoint parent-folder URL.
        name : str
            Safe single-segment child name.

        Returns
        -------
        SPFolder
            Created folder metadata.
        """
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(c in name for c in "/\\\0")
        ):
            raise SPValidationError("Folder name must be one safe path segment")
        parent = self.get_folder_metadata_from_url(url)
        response = self._request(
            "POST",
            f"{self._graph_base_url}/drives/{self._drive_id}/items/{parent.id}/children",
            headers=self._hdr(json_content=True),
            timeout=30,
            json={"name": name, "folder": {}},
        )
        try:
            self._raise_for_status(response)
            data = response.json()
        finally:
            response.close()
        self._validate_item_boundary(data)
        if "folder" not in data:
            raise SPGraphError("Graph returned a non-folder item")
        return SPFolder.from_dict(data)

    def delete_folder_from_url(self, url: str, force_delete: bool = False) -> None:
        """Delete an approved SharePoint folder.

        Parameters
        ----------
        url : str
            Approved SharePoint folder URL.
        force_delete : bool, default=False
            Delete non-empty folders when true.
        """
        self.delete_folder(
            self.get_folder_metadata_from_url(url), force_delete=force_delete
        )

    def get_folder_permissions_from_url(self, url: str) -> tuple[dict[str, Any], ...]:
        """Return normalized permissions for an approved folder URL.

        Parameters
        ----------
        url : str
            Approved SharePoint folder URL.

        Returns
        -------
        tuple[dict[str, Any], ...]
            Normalized permission records.
        """
        folder = self.get_folder_metadata_from_url(url)
        permission_url = f"{self._graph_base_url}/drives/{self._drive_id}/items/{folder.id}/permissions"
        return tuple(
            self._normalize_permission(item) for item in self._paginate(permission_url)
        )

    def get_file_permissions_from_url(self, url: str) -> tuple[dict[str, Any], ...]:
        """Return normalized permissions for an approved file URL.

        Parameters
        ----------
        url : str
            Approved SharePoint file URL.

        Returns
        -------
        tuple[dict[str, Any], ...]
            Normalized permission records.
        """
        file = self.get_file_metadata_from_url(url)
        permission_url = f"{self._graph_base_url}/drives/{self._drive_id}/items/{file.id}/permissions"
        return tuple(
            self._normalize_permission(item) for item in self._paginate(permission_url)
        )

    @staticmethod
    def _normalize_permission(permission: dict[str, Any]) -> dict[str, Any]:
        principal = permission.get("grantedToV2") or permission.get("grantedTo") or {}
        principal_type = next(
            (
                kind
                for kind in ("user", "group", "siteUser", "siteGroup", "application")
                if kind in principal
            ),
            None,
        )
        link = permission.get("link") or {}
        return {
            "id": str(permission.get("id", "")),
            "roles": tuple(str(role) for role in permission.get("roles", ())),
            "scope": link.get("scope", "direct"),
            "type": link.get("type"),
            "granted_to": {
                "type": principal_type,
                "id": principal.get(principal_type, {}).get("id")
                if principal_type
                else None,
                "display_name": principal.get(principal_type, {}).get("displayName")
                if principal_type
                else None,
            },
            "expiration_datetime": permission.get("expirationDateTime"),
        }

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
        logger.info("Downloading file")

        filename = file_obj.name if new_filename is None else new_filename
        target_path = safe_join(local_download_path, filename)
        self._download_to_path(file_obj, target_path)

        return file_obj

    def upload_file_to_url(
        self,
        sharing_url: str,
        local_file_path: str,
        conflict_behavior: Literal["fail", "replace", "rename"] = "replace",
    ) -> SPFile:
        """
        Uploads a file to SharePoint using an Upload Session.
        Works for any file size by breaking the file into 320KiB-aligned chunks.

        Parameters
        ----------
        sharing_url : str
            Approved SharePoint sharing URL for the target file.
        local_file_path : str
            Local source file path.
        conflict_behavior : {"fail", "replace", "rename"}, default="replace"
            Conflict handling applied by Graph.

        Returns
        -------
        SPFile
            Uploaded file metadata.
        """
        if conflict_behavior not in {"fail", "replace", "rename"}:
            raise SPValidationError("Invalid conflict behavior")
        # 1. Resolve the sharing URL to get Drive and Parent IDs
        file_obj = self.get_file_metadata_from_url(sharing_url)
        drive_id = file_obj.parent_reference["driveId"]
        item_id = file_obj.id

        file_size = os.path.getsize(local_file_path)
        self._check_file_budget(file_size)

        # 2. Create the Upload Session
        session_url = (
            f"{self._graph_base_url}/drives/{drive_id}"
            f"/items/{item_id}/createUploadSession"
        )
        with open(local_file_path, "rb") as source:
            return self._upload_source_resumable(
                source,
                file_obj.name,
                None,
                file_size,
                conflict_behavior,
                session_url=session_url,
                fallback=file_obj,
            )

    def upload_file_to_folder_url(
        self,
        folder_url: str,
        local_file_path: str,
        conflict_behavior: Literal["fail", "replace", "rename"] = "replace",
    ) -> SPFile:
        """Upload a local file below an approved SharePoint folder URL.

        Parameters
        ----------
        folder_url : str
            Approved SharePoint folder URL.
        local_file_path : str
            Local source file path.
        conflict_behavior : {"fail", "replace", "rename"}, default="replace"
            Conflict handling applied by Graph.

        Returns
        -------
        SPFile
            Uploaded file metadata.
        """
        folder = self.get_folder_metadata_from_url(folder_url)
        return self.upload_file(
            local_file_path,
            _folder=folder,
            conflict_behavior=conflict_behavior,
        )

    def upload_folder_to_folder_url(
        self, folder_url: str, local_folder_path: str
    ) -> None:
        """Recursively upload a local folder below an approved folder URL.

        Parameters
        ----------
        folder_url : str
            Approved SharePoint destination folder URL.
        local_folder_path : str
            Local source folder path.
        """
        folder = self.get_folder_metadata_from_url(folder_url)
        self.upload_folder(local_folder_path, _folder=folder)

    def download_folder_from_url(
        self, folder_url: str, local_download_path: str
    ) -> None:
        """Recursively download an approved SharePoint folder URL.

        Parameters
        ----------
        folder_url : str
            Approved SharePoint folder URL.
        local_download_path : str
            Local destination directory.
        """
        folder = self.get_folder_metadata_from_url(folder_url)
        self.download_folder(local_download_path, _folder=folder)

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
        self._raise_for_status(r)
        self._site_id = r.json()["id"]
        return self._site_id

    def _get_drive_id(self) -> str:
        site_id = self._site_id
        if self.document_folder_name is not None:
            for d in self._paginate(f"{self._graph_base_url}/sites/{site_id}/drives"):
                if d.get("name") == self.document_folder_name:
                    self._drive_id = d["id"]
                    self._drive_url_name = self._drive_name_from_web_url(
                        d, self.document_folder_name
                    )
                    return self._drive_id
            raise SPDriveNotFound(
                "Requested document library was not found", status=404
            )

        # Auto-detect the default document library only when no name was provided.
        r = self._request(
            "GET",
            f"{self._graph_base_url}/sites/{site_id}/drive",
            headers=self._hdr(),
            timeout=30,
        )
        self._raise_for_status(r, not_found=SPDriveNotFound)
        drive = r.json()
        self._drive_id = drive["id"]
        self.document_folder_name = unquote(drive["webUrl"].split("/")[-1])
        self._drive_url_name = self.document_folder_name
        return self._drive_id

    @staticmethod
    def _drive_name_from_web_url(drive: dict[str, Any], fallback: str) -> str:
        web_url = drive.get("webUrl")
        if isinstance(web_url, str) and web_url:
            return unquote(web_url.rstrip("/").split("/")[-1])
        return fallback

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

        self._raise_for_status(r, not_found=SPFolderNotFound)
        return SPFolder.from_dict(r.json())

    def _get_file(self, filename: str, folder: SPFolder | None = None) -> SPFile:
        """Fetch a file directly under the current folder by name (O(1) lookup)."""
        drive_id = self._drive_id
        folder_id = str((folder or self._root_folder).id)
        encoded_name = quote_segment(filename)
        url = f"{self._graph_base_url}/drives/{drive_id}/items/{folder_id}:/{encoded_name}"
        r = self._request("GET", url, headers=self._hdr(), timeout=30)
        self._raise_for_status(r, not_found=SPFileNotFound)
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
        self._raise_for_status(r)
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
        """Validate that an optional deployment grant matches this manager.

        Parameters
        ----------
        site_id : str, optional
            Expected SharePoint site identifier.
        drive_id : str, optional
            Expected document-library identifier.

        Returns
        -------
        bool
            ``True`` when all supplied identifiers match.
        """
        if site_id is not None and site_id != self._site_id:
            raise SPUnauthorizedTarget(
                "Configured site does not match the selected resource grant"
            )
        if drive_id is not None and drive_id != self._drive_id:
            raise SPUnauthorizedTarget(
                "Configured drive does not match the selected resource grant"
            )
        return True

    def _check_file_budget(
        self,
        size: int,
        destination: str | None = None,
        _budget: dict[str, Any] | None = None,
    ) -> None:
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
        if _budget is not None:
            self._consume_operation_budget(_budget, byte_count=size, items=1)

    def _consume_operation_budget(
        self,
        budget: dict[str, Any],
        *,
        byte_count: int = 0,
        items: int = 0,
        pages: int = 0,
        depth: int | None = None,
    ) -> None:
        if time.monotonic() - budget["started"] > self.policy.wall_clock_seconds:
            raise SPDeadlineExceeded("Transfer deadline exceeded", retryable=True)
        budget["bytes"] += byte_count
        budget["items"] += items
        budget["pages"] += pages
        if budget["bytes"] > self.policy.max_total_bytes:
            raise SPValidationError("Transfer byte budget exceeded")
        if budget["items"] > self.policy.max_items:
            raise SPValidationError("Transfer item budget exceeded")
        if budget["pages"] > self.policy.max_pages:
            raise SPValidationError("Transfer page budget exceeded")
        if depth is not None and depth > self.policy.max_depth:
            raise SPValidationError("Transfer depth budget exceeded")

    def _check_depth(self, path: str | None) -> None:
        depth = len(get_names_to_folder(path or ""))
        if depth > self.policy.max_depth:
            raise SPValidationError(
                "Folder depth exceeds the configured traversal budget"
            )

    def _stream_download(self, file_obj: SPFile, output: BinaryIO) -> int:
        downloaded_bytes = 0
        digest = QuickXorHash()
        with self._request(
            "GET", file_obj.download_url, stream=True, timeout=(10, 300)
        ) as response:
            self._raise_for_status(response)
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    policy = getattr(self, "policy", OperationPolicy())
                    if (
                        downloaded_bytes + len(chunk) > int(file_obj.size)
                        or downloaded_bytes + len(chunk) > policy.max_file_bytes
                        or downloaded_bytes + len(chunk) > policy.max_total_bytes
                    ):
                        raise SPValidationError(
                            "Downloaded content exceeded its budget"
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded_bytes += len(chunk)
        if downloaded_bytes != int(file_obj.size):
            raise SPValidationError("Downloaded content was incomplete")
        expected_hash = file_obj.quick_xor_hash
        if expected_hash and digest.b64digest() != expected_hash:
            raise SPFileIntegrityError(
                "Downloaded content failed integrity verification"
            )
        return downloaded_bytes

    def _download_to_path(self, file_obj: SPFile, target_path: str) -> None:
        """Stream to a sibling temporary file and atomically replace the target."""
        directory = os.path.dirname(target_path) or "."
        fd, temporary_path = tempfile.mkstemp(prefix=".sp-download-", dir=directory)
        downloaded_bytes = 0
        try:
            with os.fdopen(fd, "wb") as output:
                downloaded_bytes = self._stream_download(file_obj, output)
            os.replace(temporary_path, target_path)
            self._emit_telemetry(
                "transfer",
                operation="download",
                bytes=downloaded_bytes,
                items=1,
                outcome="success",
                partial=False,
            )
        except Exception as exc:
            self._emit_telemetry(
                "transfer",
                operation="download",
                bytes=downloaded_bytes,
                items=1,
                outcome="failure",
                partial=downloaded_bytes > 0,
                failure_class=type(exc).__name__,
            )
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def download_fileobj(
        self,
        file: str | SPFile,
        destination: BinaryIO,
        sp_relative_folder_path: str | None = None,
    ) -> SPFile:
        """Stream a file into a caller-owned binary object.

        Parameters
        ----------
        file : str or SPFile
            Filename or file metadata.
        destination : BinaryIO
            Writable binary destination.
        sp_relative_folder_path : str, optional
            Document-library path; omitted uses the root.

        Returns
        -------
        SPFile
            Downloaded file metadata.
        """
        if not hasattr(destination, "write"):
            raise TypeError("destination must be a writable binary file object")
        if isinstance(file, str):
            folder = (
                self._resolve_folder(sp_relative_folder_path)
                if sp_relative_folder_path is not None
                else self._root_folder
            )
            file_obj = self._get_file(file, folder)
        else:
            file_obj = file
            self._validate_file_boundary(file_obj)
        self._check_file_budget(int(file_obj.size))
        downloaded_bytes = self._stream_download(file_obj, destination)
        self._emit_telemetry(
            "transfer",
            operation="download",
            bytes=downloaded_bytes,
            items=1,
            outcome="success",
            partial=False,
        )
        return file_obj

    def upload_fileobj(
        self,
        source: BinaryIO,
        filename: str,
        sp_relative_folder_path: str | None = None,
        conflict_behavior: Literal["fail", "replace", "rename"] = "replace",
        _folder: SPFolder | None = None,
    ) -> SPFile:
        """Upload a caller-owned binary object through the normal upload path.

        Parameters
        ----------
        source : BinaryIO
            Readable binary source.
        filename : str
            Plain destination filename.
        sp_relative_folder_path : str, optional
            Document-library path; omitted uses the root.
        conflict_behavior : {"fail", "replace", "rename"}, default="replace"
            Conflict handling applied by Graph.

        Returns
        -------
        SPFile
            Uploaded file metadata.
        """
        if not hasattr(source, "read"):
            raise TypeError("source must be a readable binary file object")
        if not filename or os.path.basename(filename) != filename:
            raise SPValidationError("filename must be a plain file name")
        if conflict_behavior not in {"fail", "replace", "rename"}:
            raise SPValidationError("Invalid conflict behavior")

        try:
            source.seek(0, os.SEEK_END)
            file_size_b = source.tell()
            source.seek(0)
        except (AttributeError, OSError, ValueError):
            file_size_b = None
        if file_size_b is not None:
            self._check_file_budget(file_size_b)
            target_folder = _folder or (
                self._resolve_folder(sp_relative_folder_path, create_folder=True)
                if sp_relative_folder_path is not None
                else self._root_folder
            )
            if file_size_b <= _DIRECT_UPLOAD_MAX_BYTES:
                return self._upload_source_direct(
                    source,
                    filename,
                    target_folder,
                    file_size_b,
                    conflict_behavior,
                )
            return self._upload_source_resumable(
                source,
                filename,
                target_folder,
                file_size_b,
                conflict_behavior,
            )

        with tempfile.TemporaryDirectory(prefix="sp-upload-") as directory:
            path = safe_join(directory, filename)
            total = 0
            with open(path, "wb") as output:
                while chunk := source.read(_DOWNLOAD_CHUNK_SIZE):
                    total += len(chunk)
                    self._check_file_budget(total)
                    output.write(chunk)
            return self.upload_file(
                path,
                sp_relative_folder_path,
                _folder=_folder,
                conflict_behavior=conflict_behavior,
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

    def _list_children(
        self,
        folder: SPFolder | None = None,
        _budget: dict[str, Any] | None = None,
    ) -> tuple[dict[str, SPFile], dict[str, SPFolder]]:
        """Single-pass enumeration of a folder's children.

        Returns ``(files_by_name, folders_by_name)``. Saves a network round
        trip versus calling :meth:`list_files` and :meth:`list_folders`.
        """
        target = folder if folder is not None else self._root_folder
        drive_id = self._drive_id
        url = f"{self._graph_base_url}/drives/{drive_id}/items/{target.id}/children"
        files: dict[str, SPFile] = {}
        folders: dict[str, SPFolder] = {}
        for item in self._paginate(url, _budget=_budget):
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
            Relative path within the document library. If omitted, uses the document-library root.


        Returns
        -------
        dict
            Mapping of filename to SPFile objects.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> files = manager.list_files(sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        target = (
            self._resolve_folder(sp_relative_folder_path)
            if sp_relative_folder_path is not None
            else self._root_folder
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
            Relative path within the document library. If omitted, uses the document-library root.


        Returns
        -------
        dict
            Mapping of folder name to SPFolder objects.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> folders = manager.list_folders(sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        target = (
            self._resolve_folder(sp_relative_folder_path)
            if sp_relative_folder_path is not None
            else self._root_folder
        )
        _, folders = self._list_children(target)
        return folders

    # ----------------------------------------------------------
    # Upload files/folders to Sharepoint
    # ----------------------------------------------------------

    def _upload_file_direct(
        self,
        local_file_path: str,
        target_folder: SPFolder,
        file_size_b: int,
        conflict_behavior: Literal["fail", "replace", "rename"],
    ) -> SPFile:
        with open(local_file_path, "rb") as source:
            return self._upload_source_direct(
                source,
                get_filename(local_file_path),
                target_folder,
                file_size_b,
                conflict_behavior,
            )

    def _upload_source_direct(
        self,
        source: BinaryIO,
        file_name: str,
        target_folder: SPFolder,
        file_size_b: int,
        conflict_behavior: Literal["fail", "replace", "rename"],
    ) -> SPFile:
        encoded_name = quote_segment(file_name)
        url = (
            f"{self._graph_base_url}/sites/{self._site_id}/drives/{self._drive_id}"
            f"/items/{target_folder.id}:/{encoded_name}:/content"
        )
        try:
            response = self._request(
                "PUT",
                url,
                headers=self._hdr(),
                timeout=60,
                params={"@microsoft.graph.conflictBehavior": conflict_behavior},
                data=source.read(file_size_b),
            )
            self._raise_for_status(response)
            result = SPFile.from_dict(response.json())
        except Exception as exc:  # noqa: BLE001
            self._emit_telemetry(
                "transfer",
                operation="upload",
                bytes=0,
                expected_bytes=file_size_b,
                items=1,
                outcome="failure",
                partial=False,
                failure_class=type(exc).__name__,
            )
            raise SPAmbiguousWriteError() from None
        self._emit_telemetry(
            "transfer",
            operation="upload",
            bytes=file_size_b,
            expected_bytes=file_size_b,
            items=1,
            outcome="success",
            partial=False,
        )
        return result

    def _upload_source_resumable(
        self,
        source: BinaryIO,
        file_name: str,
        target_folder: SPFolder | None,
        file_size_b: int,
        conflict_behavior: Literal["fail", "replace", "rename"],
        *,
        session_url: str | None = None,
        fallback: SPFile | None = None,
    ) -> SPFile:
        if session_url is None:
            if target_folder is None:
                raise SPValidationError("Upload target folder is required")
            encoded_name = quote_segment(file_name)
            session_url = (
                f"{self._graph_base_url}/sites/{self._site_id}/drives/{self._drive_id}"
                f"/items/{target_folder.id}:/{encoded_name}:/createUploadSession"
            )
        response: requests.Response | None = None
        upload_url: str | None = None
        start_byte = 0
        try:
            session = self._request(
                "POST",
                session_url,
                headers=self._hdr(json_content=True),
                timeout=30,
                json={"item": {"@microsoft.graph.conflictBehavior": conflict_behavior}},
            )
            self._raise_for_status(session)
            upload_url = str(session.json()["uploadUrl"])
            if file_size_b == 0:
                response = self._request(
                    "PUT",
                    session_url.removesuffix("/createUploadSession") + "/content",
                    headers=self._hdr(),
                    timeout=60,
                    data=b"",
                )
                self._raise_for_status(response)
            else:
                while start_byte < file_size_b:
                    chunk = source.read(
                        min(_UPLOAD_CHUNK_SIZE, file_size_b - start_byte)
                    )
                    if not chunk:
                        raise SPValidationError(
                            "Upload source ended before its declared size"
                        )
                    end_byte = start_byte + len(chunk) - 1
                    response = self._request(
                        "PUT",
                        upload_url,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size_b}",
                        },
                        timeout=60,
                        data=chunk,
                    )
                    self._raise_for_status(response)
                    next_start = end_byte + 1
                    try:
                        ranges = response.json().get("nextExpectedRanges", [])
                        if ranges:
                            next_start = int(str(ranges[0]).split("-", 1)[0])
                    except (AttributeError, TypeError, ValueError):
                        pass
                    if next_start < 0 or next_start > file_size_b:
                        raise SPValidationError(
                            "Graph returned an invalid upload offset"
                        )
                    if next_start != end_byte + 1:
                        source.seek(next_start)
                    start_byte = next_start
        except Exception as exc:  # noqa: BLE001
            if upload_url is not None:
                try:
                    self._request("DELETE", upload_url, timeout=30)
                except Exception:  # noqa: BLE001, S110
                    pass
            self._emit_telemetry(
                "transfer",
                operation="upload",
                bytes=start_byte,
                expected_bytes=file_size_b,
                items=1,
                outcome="failure",
                partial=start_byte > 0,
                failure_class=type(exc).__name__,
            )
            raise SPAmbiguousWriteError() from None
        if response is None:
            raise SPAmbiguousWriteError()
        try:
            result = SPFile.from_dict(response.json())
        except (TypeError, KeyError, ValueError):
            if fallback is not None:
                result = fallback
            elif target_folder is not None:
                result = self._get_file(file_name, target_folder)
            else:
                raise SPAmbiguousWriteError()
        self._emit_telemetry(
            "transfer",
            operation="upload",
            bytes=file_size_b,
            expected_bytes=file_size_b,
            items=1,
            outcome="success",
            partial=False,
        )
        return result

    def upload_file(
        self,
        local_file_path: str,
        sp_relative_folder_path: str | None = None,
        _folder: SPFolder | None = None,
        conflict_behavior: Literal["fail", "replace", "rename"] = "replace",
        _budget: dict[str, Any] | None = None,
    ) -> SPFile:
        """
        Upload a local file to SharePoint.


        Parameters
        ----------
        local_file_path : str
            Path to the local file.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the document-library root.
        conflict_behavior : {"fail", "replace", "rename"}, default="replace"
            Conflict handling applied by Graph during upload.


        Raises
        ------
        FileNotFoundError
            If the local file does not exist or is not a file.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.upload_file(local_file_path = "file.txt", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        if conflict_behavior not in {"fail", "replace", "rename"}:
            raise SPValidationError("Invalid conflict behavior")
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
            else self._root_folder
        )

        file_name = get_filename(local_file_path)
        file_size_b = os.path.getsize(local_file_path)
        self._check_file_budget(file_size_b, _budget=_budget)
        file_size_mb = file_size_b / (1024 * 1024)

        if file_size_b <= _DIRECT_UPLOAD_MAX_BYTES:
            return self._upload_file_direct(
                local_file_path,
                target_folder,
                file_size_b,
                conflict_behavior,
            )

        logger.info("Uploading file (%.1f MB)", file_size_mb)
        with open(local_file_path, "rb") as file:
            return self._upload_source_resumable(
                file,
                file_name,
                target_folder,
                file_size_b,
                conflict_behavior,
            )

    def upload_folder(
        self,
        local_folder_path: str,
        sp_relative_folder_path: str | None = None,
        _folder: SPFolder | None = None,
        _depth: int = 0,
        _budget: dict[str, Any] | None = None,
    ) -> None:
        """
        Recursively upload a local folder and its contents to SharePoint.


        Parameters
        ----------
        local_folder_path : str
            Path to the local folder.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the document-library root.


        Raises
        ------
        FileNotFoundError
            If the local folder does not exist.
        ValueError
            If the path is not a folder.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.upload_folder(local_folder_path="./Folder4", sp_relative_folder_path="Folder1/Folder2/Folder3")
        """

        local_folder_path = os.path.abspath(local_folder_path)
        budget = _budget or {
            "bytes": 0,
            "items": 0,
            "pages": 0,
            "started": time.monotonic(),
        }
        depth = _depth or len(get_names_to_folder(sp_relative_folder_path or ""))
        self._consume_operation_budget(budget, items=1, depth=depth)
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
            else self._root_folder
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
            self.upload_file(file_path, _folder=target_folder, _budget=budget)

        for subdir_path in subdirs:
            self.upload_folder(
                subdir_path,
                _folder=target_folder,
                _depth=depth + 1,
                _budget=budget,
            )

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
        _budget: dict[str, Any] | None = None,
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
            Relative path within the document library. If omitted, uses the document-library root.
        new_filename : str, optional
            If provided, rename the downloaded file.


        Returns
        -------
        SPFile
            The downloaded file metadata.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.download_file(file="file.txt", local_download_path="./Download_Dir",
        ...     sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_download_path = os.path.abspath(local_download_path)
        self._check_depth(sp_relative_folder_path)

        if isinstance(file, str):
            target_folder = _folder or (
                self._resolve_folder(sp_relative_folder_path)
                if sp_relative_folder_path is not None
                else self._root_folder
            )
            file_obj = self._get_file(file, target_folder)
        else:
            file_obj = file
            self._validate_file_boundary(file_obj)

        os.makedirs(local_download_path, exist_ok=True)
        file_size_bytes = int(file_obj.size)
        self._check_file_budget(file_size_bytes, local_download_path, _budget=_budget)
        logger.info("Downloading file")

        filename = file_obj.name if new_filename is None else new_filename
        target_path = safe_join(local_download_path, filename)
        self._download_to_path(file_obj, target_path)

        return file_obj

    def download_folder(
        self,
        local_download_path: str,
        sp_relative_folder_path: str | None = None,
        _folder: SPFolder | None = None,
        _depth: int = 0,
        _budget: dict[str, Any] | None = None,
    ) -> None:
        """
        Recursively download a SharePoint folder and its contents.


        Parameters
        ----------
        local_download_path : str
            Local destination path.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the document-library root.

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
        budget = _budget or {
            "bytes": 0,
            "items": 0,
            "pages": 0,
            "started": time.monotonic(),
        }
        depth = _depth or len(get_names_to_folder(sp_relative_folder_path or ""))
        self._consume_operation_budget(budget, items=1, depth=depth)

        # Create local folder
        cur_folder = _folder or (
            self._resolve_folder(sp_relative_folder_path)
            if sp_relative_folder_path is not None
            else self._root_folder
        )
        logger.info("Downloading folder")
        cur_folder_download_path = (
            safe_join(local_download_path, cur_folder.name)
            if cur_folder.name
            else local_download_path
        )
        os.makedirs(cur_folder_download_path, exist_ok=True)

        # Single-pass enumeration to halve Graph round-trips per folder.
        files, subfolders = self._list_children(cur_folder, _budget=budget)

        for file in files.values():
            _ = self.download_file(file, cur_folder_download_path, _budget=budget)

        # Recurse using the resolved subfolder paths.
        for subfolder in subfolders.values():
            self.download_folder(
                cur_folder_download_path,
                _folder=subfolder,
                _depth=depth + 1,
                _budget=budget,
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
            Relative path within the document library. If omitted, uses the document-library root.


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
        >>> manager.delete_file(file="file.txt", sp_relative_folder_path="Folder1/Folder2/Folder3")
        """

        if isinstance(file, str):
            target_folder = (
                self._resolve_folder(sp_relative_folder_path)
                if sp_relative_folder_path is not None
                else self._root_folder
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
        self._raise_for_status(r)

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
            If provided, ``folder`` is interpreted relative to that location
            when given as a string.


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
        ...     manager.delete_folder("Folder1/Folder2/Folder3", force_delete=False)
        ... except SPFolderNotEmpty:
        ...     logging.info("Sharepoint folder is not empty")
        >>> manager.delete_folder("Folder1/Folder2/Folder3", force_delete=True)
        """

        # Resolve the target folder without mutating manager state.
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

        if not force_delete:
            files, folders = self._list_children(target)
            if files or folders:
                raise SPFolderNotEmpty("Sharepoint folder not empty")

        drive_id = self._drive_id
        folder_id = target.id
        r = self._request(
            "DELETE",
            f"{self._graph_base_url}/drives/{drive_id}/items/{folder_id}",
            headers=self._hdr(),
            timeout=30,
        )
        self._raise_for_status(r)
