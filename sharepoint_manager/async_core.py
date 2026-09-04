"""Asyncio SharePoint transfers with an injectable asynchronous HTTP client."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import tempfile
import time
import warnings
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from .core import (
    _DIRECT_UPLOAD_MAX_BYTES,
    _GRAPH_REQUEST_DEADLINE_EXCEEDED,
    SharepointManagerBase,
)
from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPFile,
    SPFolder,
    TokenProvider,
    UserDelegatedCredential,
)
from .exceptions import (
    SPAuthenticationError,
    SPAuthorizationError,
    SPConflictError,
    SPDeadlineExceeded,
    SPFileIntegrityError,
    SPFileNotFound,
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
)
from .utils import QuickXorHash, safe_join

_CHUNK_SIZE = 20 * 327680
_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT"})
_GRAPH_CONFLICT_BEHAVIOR = "@microsoft.graph.conflictBehavior"
logger = logging.getLogger(__name__)


class AsyncSharepointManager:
    """Native asyncio client for explicit URL-based SharePoint transfers.

    The HTTP client is injectable for tests and custom transports. When it is
    omitted, ``httpx.AsyncClient`` is loaded lazily, keeping package import
    independent from the optional transport until the async client is used.

    Examples
    --------
    >>> async def transfer(provider, file_url, destination):
    ...     async with AsyncSharepointManager(
    ...         "https://tenant.sharepoint.com/sites/demo",
    ...         token_provider=provider,
    ...     ) as manager:
    ...         await manager.download_file_from_url(file_url, destination)
    """

    def __init__(
        self,
        sharepoint_site_url: str,
        credentials: ClientCredential | UserDelegatedCredential | None = None,
        *,
        token_provider: TokenProvider | None = None,
        graph_host: str = "graph.microsoft.com",
        tenant_id: str | None = None,
        policy: OperationPolicy | None = None,
        client: Any | None = None,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize an async manager for one SharePoint site.

        Parameters
        ----------
        sharepoint_site_url : str
            Approved SharePoint site URL.
        credentials : ClientCredential or UserDelegatedCredential, optional
            MSAL credential. Supply ``tenant_id`` with credential-based auth.
        token_provider : TokenProvider, optional
            Injected provider used instead of MSAL.
        graph_host : str, default="graph.microsoft.com"
            Approved Microsoft Graph host.
        tenant_id : str, optional
            Tenant name or GUID used in the MSAL authority.
        policy : OperationPolicy, optional
            Transfer budgets and retry limits.
        client : object, optional
            Injected async HTTP client for tests or custom transports.
        telemetry : callable, optional
            Best-effort event callback.
        """
        SharepointManagerBase._validate_sharepoint_url(sharepoint_site_url)
        graph_host = graph_host.lower().rstrip(".")
        if graph_host not in GRAPH_HOSTS:
            raise SPValidationError(
                "graph_host must be an approved Microsoft Graph host"
            )
        if credentials is None and token_provider is None:
            raise ValueError("credentials or token_provider is required")
        if tenant_id is not None:
            tenant_id = tenant_id.strip()
            if not tenant_id or "/" in tenant_id or "\\" in tenant_id:
                raise SPValidationError("tenant_id must be a tenant name or GUID")
        if credentials is not None and token_provider is None and tenant_id is None:
            raise ValueError("tenant_id is required when credentials are used")

        self.sharepoint_site_url = sharepoint_site_url
        self.graph_host = graph_host
        self.tenant_id = tenant_id
        self._graph_base_url = f"https://{graph_host}/v1.0"
        self.policy = policy or OperationPolicy()
        self.credentials = credentials
        self._token_provider = token_provider
        self.telemetry = telemetry
        self._correlation_id = uuid4().hex
        self._client = client
        self._owns_client = client is None
        self._token_lock = asyncio.Lock()
        self._boundary_lock = asyncio.Lock()
        self._request_gate = asyncio.Semaphore(self.policy.max_concurrency)
        self._closed = False
        self._cached_token = ""
        self._cached_token_expiry = 0
        self._msal_client: Any | None = None
        self._account: Any | None = None
        self._site_id: str | None = None
        self._drive_id: str | None = None
        self._drive_url_name: str | None = None
        self._user_credentials = isinstance(credentials, UserDelegatedCredential)
        self._username = credentials.username if self._user_credentials else None
        self._password: str | None = (
            credentials.password if self._user_credentials else None
        )
        self._warned_password_auth = False

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - exercised by packaging
                raise RuntimeError(
                    "AsyncSharepointManager requires the httpx dependency"
                ) from exc
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    def _validate_graph_url(self, url: str) -> None:
        validate_graph_url(url, self.graph_host)

    def _validate_capability_url(self, url: str) -> None:
        validate_capability_url(url, self.graph_host)

    def _emit(self, event: str, **fields: Any) -> None:
        record = {
            "event": event,
            "correlation_id": self._correlation_id,
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
        if not callable(self.telemetry):
            return
        try:
            self.telemetry(record)
        except Exception:  # noqa: BLE001
            return

    async def _get_token_result(self) -> Any:
        if self._token_provider is not None:
            result = self._token_provider.get_token(
                f"https://{self.graph_host}/.default"
            )
            if inspect.isawaitable(result):
                result = await result
            return result
        return await self._acquire_msal_token()

    def _cache_token(self, result: Any, now: int) -> str:
        if not isinstance(result, dict):
            expires_on = int(getattr(result, "expires_on", 0) or 0)
            result = {
                "access_token": getattr(result, "token", None),
                "expires_on": expires_on,
                "expires_in": max(expires_on - now, 60) if expires_on else 3600,
            }
        if "access_token" not in result:
            raise SPAuthenticationError("Authentication failed")
        self._cached_token = str(result["access_token"])
        expires_on = int(result.get("expires_on", 0) or 0)
        if not expires_on:
            expires_on = now + max(int(result.get("expires_in", 3600) or 3600), 60)
        self._cached_token_expiry = expires_on
        return self._cached_token

    async def _ensure_token(self) -> str:
        now = int(time.time())
        if self._cached_token and self._cached_token_expiry - now > 120:
            return self._cached_token

        async with self._token_lock:
            now = int(time.time())
            if self._cached_token and self._cached_token_expiry - now > 120:
                return self._cached_token

            result = await self._get_token_result()
            self._cache_token(result, now)
            self._emit("auth.token_refresh", success=True)
            return self._cached_token

    async def _ensure_msal_client(self) -> None:
        if self._msal_client is None:
            try:
                from msal import ConfidentialClientApplication, PublicClientApplication
            except ImportError as exc:  # pragma: no cover - packaging environment
                raise RuntimeError(
                    "MSAL is required for credential-based authentication"
                ) from exc
            if isinstance(self.credentials, ClientCredential):
                self._msal_client = ConfidentialClientApplication(
                    self.credentials.client_id,
                    client_credential=self.credentials.client_secret,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                )
            elif self._user_credentials:
                self._msal_client = PublicClientApplication(
                    self.credentials.client_id,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                )
            else:
                raise SPAuthenticationError("Unsupported credentials")

    async def _get_msal_account(self) -> Any:
        if self._account is None:
            accounts = await asyncio.to_thread(
                self._msal_client.get_accounts, username=self._username
            )
            self._account = accounts[0] if accounts else None
        return self._account

    async def _acquire_password_token(self, scopes: list[str]) -> dict[str, Any]:
        if not self._warned_password_auth:
            warnings.warn(
                "Username/password authentication is deprecated and does not support MFA or Conditional Access; the password is used only for initial token bootstrap.",
                UserWarning,
                stacklevel=3,
            )
            self._warned_password_auth = True
        if self._password is None:
            raise SPAuthenticationError(
                "Silent authentication failed; credentials must be supplied again"
            )
        result = await asyncio.to_thread(
            self._msal_client.acquire_token_by_username_password,
            username=self._username,
            password=self._password,
            scopes=scopes,
        )
        if isinstance(result, dict) and "access_token" in result:
            self._password = None
            self.credentials = None
            await self._get_msal_account()
        return result

    async def _acquire_user_token(self, scopes: list[str]) -> dict[str, Any]:
        account = await self._get_msal_account()
        if account is not None:
            result = await asyncio.to_thread(
                self._msal_client.acquire_token_silent,
                scopes=scopes,
                account=account,
            )
            if result and "access_token" in result:
                return result
        return await self._acquire_password_token(scopes)

    async def _acquire_msal_token(self) -> dict[str, Any]:
        await self._ensure_msal_client()
        scopes = [f"https://{self.graph_host}/.default"]
        if self._user_credentials:
            return await self._acquire_user_token(scopes)

        return await asyncio.to_thread(
            self._msal_client.acquire_token_for_client, scopes=scopes
        )

    async def _request(
        self, method: str, url: str, *, authenticated: bool = True, **kwargs: Any
    ) -> Any:
        if self._closed:
            raise SPValidationError("SharePoint manager is closed")
        if authenticated:
            self._validate_graph_url(url)
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = f"Bearer {await self._ensure_token()}"
            kwargs["headers"] = headers
        else:
            self._validate_capability_url(url)
        kwargs.setdefault("timeout", self.policy.wall_clock_seconds)
        async with self._request_gate:
            started = time.monotonic()
            try:
                response = await (await self._get_client()).request(
                    method, url, **kwargs
                )
            except Exception:  # noqa: BLE001
                message = (
                    "Capability request failed"
                    if not authenticated
                    else "Graph request failed"
                )
                raise SPGraphError(message) from None
            if time.monotonic() - started > self.policy.wall_clock_seconds:
                request_id = response.headers.get("request-id") or response.headers.get(
                    "client-request-id"
                )
                if hasattr(response, "aclose"):
                    await response.aclose()
                raise SPDeadlineExceeded(
                    _GRAPH_REQUEST_DEADLINE_EXCEEDED,
                    status=getattr(response, "status_code", None),
                    request_id=request_id,
                    retryable=True,
                )
            return response

    @staticmethod
    async def _close_response(response: Any) -> None:
        close = getattr(response, "aclose", None)
        if close is None:
            close = getattr(response, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _retry_request(self, method: str, url: str, **kwargs: Any) -> Any:
        deadline = time.monotonic() + self.policy.wall_clock_seconds
        for attempt in range(self.policy.max_retry_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SPDeadlineExceeded(
                    _GRAPH_REQUEST_DEADLINE_EXCEEDED, retryable=True
                )
            request_kwargs = dict(kwargs)
            request_kwargs["timeout"] = min(
                float(request_kwargs.get("timeout", remaining)), remaining
            )
            try:
                response = await asyncio.wait_for(
                    self._request(method, url, **request_kwargs), remaining
                )
            except asyncio.TimeoutError as exc:
                raise SPDeadlineExceeded(
                    _GRAPH_REQUEST_DEADLINE_EXCEEDED, retryable=True
                ) from exc
            if time.monotonic() >= deadline:
                request_id = response.headers.get("request-id") or response.headers.get(
                    "client-request-id"
                )
                await self._close_response(response)
                raise SPDeadlineExceeded(
                    _GRAPH_REQUEST_DEADLINE_EXCEEDED,
                    status=getattr(response, "status_code", None),
                    request_id=request_id,
                    retryable=True,
                )
            if (
                method.upper() not in _RETRY_METHODS
                or response.status_code not in _RETRY_STATUSES
                or attempt + 1 >= self.policy.max_retry_attempts
            ):
                return response
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_seconds = float(retry_after)
            except (TypeError, ValueError):
                retry_after_seconds = 2**attempt
            remaining = deadline - time.monotonic()
            delay = min(
                max(0.0, retry_after_seconds),
                self.policy.max_retry_after_seconds,
                max(0.0, remaining),
            )
            if delay >= remaining:
                await self._close_response(response)
                raise SPDeadlineExceeded(
                    _GRAPH_REQUEST_DEADLINE_EXCEEDED,
                    status=response.status_code,
                    request_id=response.headers.get("request-id"),
                    retryable=True,
                )
            await self._close_response(response)
            await asyncio.sleep(delay)
        raise SPGraphError("Request retry budget exhausted")

    def _raise_for_status(
        self,
        response: Any,
        *,
        not_found: type[SPNotFoundError] = SPNotFoundError,
    ) -> None:
        status = int(response.status_code)
        if status < 400:
            return
        if status in {401, 403}:
            error_type = SPAuthorizationError
            message = "Graph resource request failed"
        elif status == 404:
            error_type = not_found
            message = "Graph resource request failed"
        elif status == 409:
            error_type = SPConflictError
            message = "Graph write conflicted"
        elif status == 429:
            error_type = SPThrottledError
            message = "Graph request was throttled"
        else:
            error_type = SPGraphError
            message = "Graph request failed"
        retryable = status == 429 or status >= 500
        self._emit(
            "graph.error",
            operation="request",
            status=status,
            failure_class=error_type.__name__,
            retryable=retryable,
        )
        request_id = response.headers.get("request-id") or response.headers.get(
            "client-request-id"
        )
        detail = safe_graph_error_detail(response)
        if detail:
            message = f"{message}: {detail}"
        raise error_type(
            message,
            status=status,
            request_id=request_id,
            retryable=retryable,
        )

    @staticmethod
    def _share_id(url: str) -> str:
        return share_id(url)

    async def _ensure_boundary(self) -> None:
        if self._site_id and self._drive_id:
            return
        async with self._boundary_lock:
            if self._site_id and self._drive_id:
                return
            parsed = urlsplit(self.sharepoint_site_url)
            site_path = quote(parsed.path.rstrip("/"), safe="/")
            response = await self._retry_request(
                "GET",
                f"{self._graph_base_url}/sites/{parsed.hostname}:{site_path}",
            )
            self._raise_for_status(response, not_found=SPFolderNotFound)
            site_id = response.json().get("id")
            if not site_id:
                raise SPUnauthorizedTarget("Configured SharePoint site has no ID")
            response = await self._retry_request(
                "GET", f"{self._graph_base_url}/sites/{site_id}/drive"
            )
            self._raise_for_status(response, not_found=SPFolderNotFound)
            drive = response.json()
            drive_id = drive.get("id")
            if not drive_id:
                raise SPUnauthorizedTarget("Configured SharePoint drive has no ID")
            self._site_id = site_id
            self._drive_id = drive_id
            web_url = drive.get("webUrl")
            if isinstance(web_url, str) and web_url:
                self._drive_url_name = unquote(
                    urlsplit(web_url).path.rstrip("/").split("/")[-1]
                )
            else:
                self._drive_url_name = str(drive.get("name", ""))

    def _validate_boundary(self, item: dict[str, Any]) -> None:
        SharepointManagerBase._validate_item_boundary(self, item)

    async def _request_item(
        self, endpoint: str, *, not_found: type[SPNotFoundError]
    ) -> dict[str, Any]:
        response = await self._retry_request("GET", endpoint)
        try:
            self._raise_for_status(response, not_found=not_found)
            item = response.json()
        finally:
            await self._close_response(response)
        self._validate_boundary(item)
        return item

    async def _get_item_from_url(
        self, url: str, *, not_found: type[SPNotFoundError] = SPNotFoundError
    ) -> dict[str, Any]:
        SharepointManagerBase._validate_sharepoint_url(url)
        await self._ensure_boundary()
        relative_path = sharepoint_location_path(
            url,
            self.sharepoint_site_url,
            self._drive_url_name or "",
        )
        if relative_path is not None:
            endpoint = f"{self._graph_base_url}/drives/{self._drive_id}/root"
            if relative_path:
                endpoint += f":/{quote(relative_path, safe='/')}"
            return await self._request_item(endpoint, not_found=not_found)
        return await self._request_item(
            f"https://{self.graph_host}/v1.0/shares/{self._share_id(url)}/driveItem",
            not_found=not_found,
        )

    async def _children(
        self, folder: SPFolder, budget: dict[str, Any] | None = None
    ) -> tuple[dict[str, SPFile], dict[str, SPFolder]]:
        budget = budget or {
            "bytes": 0,
            "items": 0,
            "pages": 0,
            "started": time.monotonic(),
        }
        drive_id = folder.parent_reference.get("driveId")
        if not drive_id:
            raise SPValidationError("Folder has no trusted drive reference")
        files: dict[str, SPFile] = {}
        folders: dict[str, SPFolder] = {}
        next_url: str | None = (
            f"{self._graph_base_url}/drives/{drive_id}/items/{folder.id}/children"
        )
        seen: set[str] = set()
        item_count = 0
        while next_url:
            if not isinstance(next_url, str) or next_url in seen:
                raise SPValidationError("Invalid or repeated Graph pagination link")
            seen.add(next_url)
            self._consume_budget(budget, pages=1)
            self._validate_graph_url(next_url)
            response = await self._retry_request("GET", next_url)
            try:
                self._raise_for_status(response, not_found=SPFolderNotFound)
                data = response.json()
            finally:
                await self._close_response(response)
            values = data.get("value", [])
            item_count += len(values)
            if item_count > self.policy.max_items:
                raise SPValidationError("Graph item budget exceeded")
            for item in values:
                self._validate_boundary(item)
                if "file" in item:
                    file = SPFile.from_dict(item)
                    files[file.name] = file
                elif "folder" in item:
                    child = SPFolder.from_dict(item)
                    folders[child.name] = child
            next_url = data.get("@odata.nextLink")
            if next_url is not None and not isinstance(next_url, str):
                raise SPValidationError("Invalid Graph pagination link")
        return files, folders

    def _consume_budget(
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

    async def _download_item(
        self, item: SPFile, destination: str, budget: dict[str, Any]
    ) -> None:
        self._validate_capability_url(item.download_url)
        if int(item.size) > min(
            self.policy.max_file_bytes,
            self.policy.max_total_bytes,
            self.policy.max_disk_bytes,
        ):
            raise SPValidationError("File exceeds the configured transfer budget")
        directory = os.path.dirname(destination) or "."
        fd, temporary = tempfile.mkstemp(prefix=".sp-async-download-", dir=directory)
        downloaded = 0
        digest = QuickXorHash()
        try:
            client = await self._get_client()
            if hasattr(client, "stream"):
                async with self._request_gate:
                    try:
                        response_context = client.stream("GET", item.download_url)
                        async with response_context as response:
                            self._raise_for_status(response)
                            chunks: AsyncIterator[bytes] = response.aiter_bytes(
                                _DOWNLOAD_CHUNK_SIZE
                            )
                            with os.fdopen(fd, "wb") as output:
                                fd = None
                                async for chunk in chunks:
                                    await self._consume_chunk(
                                        output,
                                        chunk,
                                        digest,
                                        budget,
                                        downloaded,
                                        int(item.size),
                                    )
                                    downloaded += len(chunk)
                    except (
                        OSError,
                        SPValidationError,
                        SPFileIntegrityError,
                        SPGraphError,
                    ):
                        raise
                    except Exception:  # noqa: BLE001
                        raise SPGraphError("Capability request failed") from None
            else:
                response = await self._request(
                    "GET", item.download_url, authenticated=False
                )
                self._raise_for_status(response)
                with os.fdopen(fd, "wb") as output:
                    fd = None
                    chunk = response.content
                    await self._consume_chunk(
                        output, chunk, digest, budget, downloaded, int(item.size)
                    )
                    downloaded = len(chunk)
            if downloaded != int(item.size):
                raise SPValidationError("Downloaded content was incomplete")
            if item.quick_xor_hash and digest.b64digest() != item.quick_xor_hash:
                raise SPFileIntegrityError(
                    "Downloaded content failed integrity verification"
                )
            os.replace(temporary, destination)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    async def _consume_chunk(
        self,
        output: Any,
        chunk: bytes,
        digest: QuickXorHash,
        budget: dict[str, Any],
        downloaded: int = 0,
        declared_size: int | None = None,
    ) -> None:
        if not chunk:
            return
        next_size = downloaded + len(chunk)
        if declared_size is not None and next_size > declared_size:
            raise SPValidationError("Downloaded content exceeded its declared size")
        if next_size > self.policy.max_file_bytes:
            raise SPValidationError("Downloaded content exceeded its file budget")
        if budget["bytes"] + len(chunk) > self.policy.max_total_bytes:
            raise SPValidationError("Transfer byte budget exceeded")
        self._consume_budget(budget, byte_count=len(chunk))
        output.write(chunk)
        digest.update(chunk)

    async def download_file_from_url(self, url: str, destination: str) -> SPFile:
        """Download one approved SharePoint file to an explicit path.

        Parameters
        ----------
        url : str
            SharePoint file URL.
        destination : str
            Destination filename. Parent directories are created as needed.

        Returns
        -------
        SPFile
            Downloaded file metadata.
        """
        item = SPFile.from_dict(
            await self._get_item_from_url(url, not_found=SPFileNotFound)
        )
        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        budget = {"bytes": 0, "items": 0, "pages": 0, "started": time.monotonic()}
        self._consume_budget(budget, items=1)
        await self._download_item(item, destination, budget)
        self._emit(
            "transfer",
            operation="download",
            bytes=item.size,
            items=1,
            outcome="success",
        )
        return item

    async def _create_folder(self, folder: SPFolder, name: str) -> SPFolder:
        drive_id = folder.parent_reference.get("driveId")
        response = await self._retry_request(
            "POST",
            f"{self._graph_base_url}/drives/{drive_id}/items/{folder.id}/children",
            headers={"Content-Type": "application/json"},
            json={
                "name": name,
                "folder": {},
                _GRAPH_CONFLICT_BEHAVIOR: "fail",
            },
        )
        self._raise_for_status(response)
        payload = response.json()
        self._validate_boundary(payload)
        return SPFolder.from_dict(payload)

    async def _upload_file(
        self,
        folder: SPFolder,
        local_path: str,
        budget: dict[str, Any] | None = None,
    ) -> SPFile:
        path = Path(local_path)
        if not path.is_file() or path.is_symlink():
            raise SPValidationError("Upload source must be a regular file")
        size = path.stat().st_size
        if size > self.policy.max_file_bytes:
            raise SPValidationError("File exceeds the configured transfer budget")
        if budget is not None:
            self._consume_budget(budget, byte_count=size, items=1)
        drive_id = folder.parent_reference.get("driveId")
        if size <= _DIRECT_UPLOAD_MAX_BYTES:
            url = (
                f"{self._graph_base_url}/drives/{drive_id}/items/{folder.id}:"
                f"/{quote(path.name, safe='')}:/content"
            )
            response = await self._retry_request(
                "PUT",
                url,
                headers={"Content-Length": str(size)},
                params={_GRAPH_CONFLICT_BEHAVIOR: "replace"},
                content=path.read_bytes(),
            )
            try:
                self._raise_for_status(response)
                payload = response.json()
            finally:
                await self._close_response(response)
            return (
                SPFile.from_dict(payload)
                if payload.get("id")
                else SPFile(id="", name=path.name, size=size)
            )
        session_response = await self._retry_request(
            "POST",
            f"{self._graph_base_url}/drives/{drive_id}/items/{folder.id}:/{path.name}:/createUploadSession",
            headers={"Content-Type": "application/json"},
            json={"item": {_GRAPH_CONFLICT_BEHAVIOR: "replace"}},
        )
        self._raise_for_status(session_response)
        upload_url = session_response.json()["uploadUrl"]
        offset = 0
        response = None
        with path.open("rb") as source:  # noqa: ASYNC230
            while chunk := source.read(_CHUNK_SIZE):
                end = offset + len(chunk) - 1
                response = await self._retry_request(
                    "PUT",
                    upload_url,
                    authenticated=False,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    content=chunk,
                )
                self._raise_for_status(response)
                offset = end + 1
        payload = response.json() if response is not None else {}
        return (
            SPFile.from_dict(payload)
            if payload.get("id")
            else SPFile(id="", name=path.name, size=size)
        )

    async def upload_file_to_folder_url(
        self, folder_url: str, local_path: str
    ) -> SPFile:
        """Upload one local file below an approved folder URL.

        Parameters
        ----------
        folder_url : str
            Approved SharePoint folder URL.
        local_path : str
            Local regular-file path.

        Returns
        -------
        SPFile
            Uploaded file metadata.
        """
        folder = SPFolder.from_dict(
            await self._get_item_from_url(folder_url, not_found=SPFolderNotFound)
        )
        budget = {"bytes": 0, "items": 0, "pages": 0, "started": time.monotonic()}
        result = await self._upload_file(folder, local_path, budget)
        self._emit(
            "transfer",
            operation="upload",
            bytes=result.size,
            items=1,
            outcome="success",
        )
        return result

    async def download_folder_from_url(self, folder_url: str, destination: str) -> None:
        """Recursively download an approved folder to a local path.

        Parameters
        ----------
        folder_url : str
            Approved SharePoint folder URL.
        destination : str
            Local destination directory.
        """
        folder = SPFolder.from_dict(
            await self._get_item_from_url(folder_url, not_found=SPFolderNotFound)
        )
        budget = {"bytes": 0, "items": 0, "pages": 0, "started": time.monotonic()}
        await self._download_folder(folder, destination, budget, depth=0)

    async def _download_folder(
        self,
        folder: SPFolder,
        destination: str,
        budget: dict[str, Any],
        depth: int,
    ) -> None:
        self._consume_budget(budget, items=1, depth=depth)
        target = safe_join(destination, folder.name) if folder.name else destination
        os.makedirs(target, exist_ok=True)
        files, folders = await self._children(folder, budget)
        for file in files.values():
            self._consume_budget(budget, items=1)
            await self._download_item(file, safe_join(target, file.name), budget)
        for child in folders.values():
            await self._download_folder(child, target, budget, depth + 1)

    async def upload_folder_to_folder_url(
        self, folder_url: str, local_path: str
    ) -> None:
        """Recursively upload a local folder below a SharePoint folder.

        Parameters
        ----------
        folder_url : str
            Approved SharePoint destination folder URL.
        local_path : str
            Local regular-folder path.
        """
        source = Path(local_path)
        if not source.is_dir() or source.is_symlink():
            raise SPValidationError("Upload source must be a regular folder")
        folder = SPFolder.from_dict(
            await self._get_item_from_url(folder_url, not_found=SPFolderNotFound)
        )
        budget = {"bytes": 0, "items": 0, "pages": 0, "started": time.monotonic()}
        await self._upload_folder(folder, source, budget, depth=0)

    async def _upload_folder(
        self,
        target: SPFolder,
        source: Path,
        budget: dict[str, Any],
        depth: int,
    ) -> None:
        self._consume_budget(budget, items=1, depth=depth)
        child = await self._create_folder(target, source.name)
        for entry in sorted(source.iterdir(), key=lambda value: value.name):
            if entry.is_symlink():
                raise SPValidationError("Symlinks are not allowed in upload trees")
            if entry.is_file():
                await self._upload_file(child, str(entry), budget)
            elif entry.is_dir():
                await self._upload_folder(child, entry, budget, depth + 1)

    async def close(self) -> None:
        """Close the owned HTTP client and clear credential state."""
        if self._closed:
            return
        self._closed = True
        self._cached_token = ""
        self._cached_token_expiry = 0
        self._password = None
        self.credentials = None
        self._account = None
        self._msal_client = None
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncSharepointManager:  # noqa: PYI034
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
