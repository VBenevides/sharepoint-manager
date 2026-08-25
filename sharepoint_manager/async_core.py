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
from typing import Any
from uuid import uuid4

from .core import SharepointManagerBase
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
    SPFileIntegrityError,
    SPFileNotFound,
    SPFolderNotFound,
    SPGraphError,
    SPThrottledError,
    SPValidationError,
)
from .urls import GRAPH_HOSTS, share_id, validate_capability_url, validate_graph_url
from .utils import QuickXorHash, safe_join


_CHUNK_SIZE = 20 * 327680
_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024
_RETRY_STATUSES = {429, 500, 502, 503, 504}
logger = logging.getLogger(__name__)


class AsyncSharepointManager:
    """Native asyncio client for explicit URL-based SharePoint transfers.

    The HTTP client is injectable for tests and custom transports. When it is
    omitted, ``httpx.AsyncClient`` is loaded lazily, keeping package import
    independent from the optional transport until the async client is used.
    """

    def __init__(
        self,
        sharepoint_site_url: str,
        credentials: ClientCredential | UserDelegatedCredential | None = None,
        *,
        token_provider: TokenProvider | None = None,
        graph_host: str = "graph.microsoft.com",
        policy: OperationPolicy | None = None,
        client: Any | None = None,
        telemetry: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        SharepointManagerBase._validate_sharepoint_url(sharepoint_site_url)
        graph_host = graph_host.lower().rstrip(".")
        if graph_host not in GRAPH_HOSTS:
            raise SPValidationError(
                "graph_host must be an approved Microsoft Graph host"
            )
        if credentials is None and token_provider is None:
            raise ValueError("credentials or token_provider is required")

        self.sharepoint_site_url = sharepoint_site_url
        self.graph_host = graph_host
        self._graph_base_url = f"https://{graph_host}/v1.0"
        self.policy = policy or OperationPolicy()
        self.credentials = credentials
        self._token_provider = token_provider
        self.telemetry = telemetry
        self._correlation_id = uuid4().hex
        self._client = client
        self._owns_client = client is None
        self._token_lock = asyncio.Lock()
        self._request_gate = asyncio.Semaphore(self.policy.max_concurrency)
        self._closed = False
        self._cached_token = ""
        self._cached_token_expiry = 0
        self._msal_client: Any | None = None
        self._account: Any | None = None
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
        except Exception:
            return

    async def _ensure_token(self) -> str:
        now = int(time.time())
        if self._cached_token and self._cached_token_expiry - now > 120:
            return self._cached_token

        async with self._token_lock:
            now = int(time.time())
            if self._cached_token and self._cached_token_expiry - now > 120:
                return self._cached_token

            if self._token_provider is not None:
                result = self._token_provider.get_token(
                    f"https://{self.graph_host}/.default"
                )
                if inspect.isawaitable(result):
                    result = await result
            else:
                result = await self._acquire_msal_token()

            if not isinstance(result, dict):
                token = getattr(result, "token", None)
                expires_on = int(getattr(result, "expires_on", 0) or 0)
                result = {
                    "access_token": token,
                    "expires_on": expires_on,
                    "expires_in": max(expires_on - now, 60) if expires_on else 3600,
                }
            if not isinstance(result, dict) or "access_token" not in result:
                detail = (
                    result.get("error_description", "Authentication failed")
                    if isinstance(result, dict)
                    else "Authentication failed"
                )
                raise SPAuthenticationError(str(detail))
            self._cached_token = str(result["access_token"])
            expires_on = int(result.get("expires_on", 0) or 0)
            if not expires_on:
                expires_on = now + max(int(result.get("expires_in", 3600) or 3600), 60)
            self._cached_token_expiry = expires_on
            self._emit("auth.token_refresh", success=True)
            return self._cached_token

    async def _acquire_msal_token(self) -> dict[str, Any]:
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
                    authority="https://login.microsoftonline.com/common",
                )
            elif self._user_credentials:
                self._msal_client = PublicClientApplication(
                    self.credentials.client_id,
                    authority="https://login.microsoftonline.com/common",
                )
            else:
                raise SPAuthenticationError("Unsupported credentials")

        scopes = [f"https://{self.graph_host}/.default"]
        if self._user_credentials:
            if self._account is None:
                accounts = await asyncio.to_thread(
                    self._msal_client.get_accounts, username=self._username
                )
                self._account = accounts[0] if accounts else None
            if self._account is not None:
                result = await asyncio.to_thread(
                    self._msal_client.acquire_token_silent,
                    scopes=scopes,
                    account=self._account,
                )
                if result and "access_token" in result:
                    return result
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
                accounts = await asyncio.to_thread(
                    self._msal_client.get_accounts, username=self._username
                )
                self._account = accounts[0] if accounts else None
            return result

        return await asyncio.to_thread(
            self._msal_client.acquire_token_for_client, scopes=scopes
        )

    async def _request(
        self, method: str, url: str, *, authenticated: bool = True, **kwargs: Any
    ) -> Any:
        if authenticated:
            self._validate_graph_url(url)
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = f"Bearer {await self._ensure_token()}"
            kwargs["headers"] = headers
        else:
            self._validate_capability_url(url)
        async with self._request_gate:
            return await (await self._get_client()).request(method, url, **kwargs)

    async def _retry_request(self, method: str, url: str, **kwargs: Any) -> Any:
        for attempt in range(self.policy.max_retry_attempts):
            response = await self._request(method, url, **kwargs)
            if (
                response.status_code not in _RETRY_STATUSES
                or attempt + 1 >= self.policy.max_retry_attempts
            ):
                return response
            await asyncio.sleep(min(2**attempt, self.policy.max_retry_after_seconds))
        raise SPGraphError("Request retry budget exhausted")

    def _raise_for_status(self, response: Any) -> None:
        status = int(response.status_code)
        if status < 400:
            return
        if status == 401:
            error_type = SPAuthenticationError
            message = "Graph authentication failed"
        elif status in {403, 404}:
            error_type = SPAuthorizationError if status == 403 else SPFileNotFound
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
        raise error_type(message, status=status, retryable=retryable)

    @staticmethod
    def _share_id(url: str) -> str:
        return share_id(url)

    async def _get_item_from_url(self, url: str) -> dict[str, Any]:
        SharepointManagerBase._validate_sharepoint_url(url)
        response = await self._retry_request(
            "GET",
            f"https://{self.graph_host}/v1.0/shares/{self._share_id(url)}/driveItem",
        )
        if response.status_code == 404:
            raise SPFolderNotFound("SharePoint URL was not found")
        self._raise_for_status(response)
        return response.json()

    async def _children(
        self, folder: SPFolder
    ) -> tuple[dict[str, SPFile], dict[str, SPFolder]]:
        drive_id = folder.parent_reference.get("driveId")
        if not drive_id:
            raise SPValidationError("Folder has no trusted drive reference")
        response = await self._retry_request(
            "GET",
            f"{self._graph_base_url}/drives/{drive_id}/items/{folder.id}/children",
        )
        self._raise_for_status(response)
        files: dict[str, SPFile] = {}
        folders: dict[str, SPFolder] = {}
        for item in response.json().get("value", []):
            if "file" in item:
                file = SPFile.from_dict(item)
                files[file.name] = file
            elif "folder" in item:
                child = SPFolder.from_dict(item)
                folders[child.name] = child
        return files, folders

    async def _download_item(
        self, item: SPFile, destination: str, budget: dict[str, Any]
    ) -> None:
        self._validate_capability_url(item.download_url)
        if int(item.size) > self.policy.max_file_bytes:
            raise SPValidationError("File exceeds the configured transfer budget")
        directory = os.path.dirname(destination) or "."
        fd, temporary = tempfile.mkstemp(prefix=".sp-async-download-", dir=directory)
        downloaded = 0
        digest = QuickXorHash()
        try:
            client = await self._get_client()
            if hasattr(client, "stream"):
                response_context = client.stream("GET", item.download_url)
                async with response_context as response:
                    self._raise_for_status(response)
                    chunks: AsyncIterator[bytes] = response.aiter_bytes(
                        _DOWNLOAD_CHUNK_SIZE
                    )
                    with os.fdopen(fd, "wb") as output:
                        async for chunk in chunks:
                            await self._consume_chunk(output, chunk, digest, budget)
                            downloaded += len(chunk)
            else:
                response = await self._request(
                    "GET", item.download_url, authenticated=False
                )
                self._raise_for_status(response)
                with os.fdopen(fd, "wb") as output:
                    chunk = response.content
                    await self._consume_chunk(output, chunk, digest, budget)
                    downloaded = len(chunk)
            if downloaded != int(item.size):
                raise SPValidationError("Downloaded content was incomplete")
            if item.quick_xor_hash and digest.b64digest() != item.quick_xor_hash:
                raise SPFileIntegrityError(
                    "Downloaded content failed integrity verification"
                )
            os.replace(temporary, destination)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    async def _consume_chunk(
        self, output: Any, chunk: bytes, digest: QuickXorHash, budget: dict[str, Any]
    ) -> None:
        if not chunk:
            return
        budget["bytes"] += len(chunk)
        if budget["bytes"] > self.policy.max_total_bytes:
            raise SPValidationError("Operation exceeds the configured byte budget")
        output.write(chunk)
        digest.update(chunk)

    async def download_file_from_url(self, url: str, destination: str) -> SPFile:
        item = SPFile.from_dict(await self._get_item_from_url(url))
        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        await self._download_item(item, destination, {"bytes": 0, "items": 0})
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
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
        self._raise_for_status(response)
        return SPFolder.from_dict(response.json())

    async def _upload_file(self, folder: SPFolder, local_path: str) -> SPFile:
        path = Path(local_path)
        if not path.is_file() or path.is_symlink():
            raise SPValidationError("Upload source must be a regular file")
        size = path.stat().st_size
        if size > self.policy.max_file_bytes:
            raise SPValidationError("File exceeds the configured transfer budget")
        drive_id = folder.parent_reference.get("driveId")
        session_response = await self._retry_request(
            "POST",
            f"{self._graph_base_url}/drives/{drive_id}/items/{folder.id}:/{path.name}:/createUploadSession",
            headers={"Content-Type": "application/json"},
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        )
        self._raise_for_status(session_response)
        upload_url = session_response.json()["uploadUrl"]
        offset = 0
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK_SIZE):
                end = offset + len(chunk) - 1
                response = await self._request(
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
        payload = response.json() if hasattr(response, "json") else {}
        return (
            SPFile.from_dict(payload)
            if payload.get("id")
            else SPFile(id="", name=path.name, size=size)
        )

    async def upload_file_to_folder_url(
        self, folder_url: str, local_path: str
    ) -> SPFile:
        folder = SPFolder.from_dict(await self._get_item_from_url(folder_url))
        result = await self._upload_file(folder, local_path)
        self._emit(
            "transfer",
            operation="upload",
            bytes=result.size,
            items=1,
            outcome="success",
        )
        return result

    async def download_folder_from_url(self, folder_url: str, destination: str) -> None:
        folder = SPFolder.from_dict(await self._get_item_from_url(folder_url))
        budget = {"bytes": 0, "items": 0}
        await self._download_folder(folder, destination, budget)

    async def _download_folder(
        self, folder: SPFolder, destination: str, budget: dict[str, Any]
    ) -> None:
        target = safe_join(destination, folder.name) if folder.name else destination
        os.makedirs(target, exist_ok=True)
        files, folders = await self._children(folder)
        for file in files.values():
            budget["items"] += 1
            if budget["items"] > self.policy.max_items:
                raise SPValidationError("Operation exceeds the configured item budget")
            await self._download_item(file, safe_join(target, file.name), budget)
        for child in folders.values():
            budget["items"] += 1
            if budget["items"] > self.policy.max_items:
                raise SPValidationError("Operation exceeds the configured item budget")
            await self._download_folder(child, target, budget)

    async def upload_folder_to_folder_url(
        self, folder_url: str, local_path: str
    ) -> None:
        source = Path(local_path)
        if not source.is_dir() or source.is_symlink():
            raise SPValidationError("Upload source must be a regular folder")
        folder = SPFolder.from_dict(await self._get_item_from_url(folder_url))
        await self._upload_folder(folder, source)

    async def _upload_folder(self, target: SPFolder, source: Path) -> None:
        child = await self._create_folder(target, source.name)
        for entry in sorted(source.iterdir(), key=lambda value: value.name):
            if entry.is_symlink():
                raise SPValidationError("Symlinks are not allowed in upload trees")
            if entry.is_file():
                await self._upload_file(child, str(entry))
            elif entry.is_dir():
                await self._upload_folder(child, entry)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cached_token = ""
        self._cached_token_expiry = 0
        self._password = None
        self._account = None
        self._msal_client = None
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncSharepointManager":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
