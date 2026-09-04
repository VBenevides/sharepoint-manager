import asyncio
import os
import sys
import tempfile
import time
import types
import warnings
from contextlib import suppress
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager import (
    AsyncSharepointManager,
    ClientCredential,
    OperationPolicy,
    async_core,
)
from sharepoint_manager.core import _DIRECT_UPLOAD_MAX_BYTES
from sharepoint_manager.exceptions import (
    SPAuthenticationError,
    SPDeadlineExceeded,
    SPGraphError,
    SPUnauthorizedTarget,
    SPValidationError,
)

_DEMO_SITE_URL = "https://tenant.sharepoint.com/sites/demo"
_GRAPH_ITEMS_URL = "https://graph.microsoft.com/v1.0/items"


class Response:
    def __init__(self, payload=None, content=b"", status_code=200):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.headers = {}

    def json(self):
        return self._payload


class Client:
    def __init__(self):
        self.items = {}
        self.children = {}
        self.uploaded = {}
        self.requests = []
        self.request_latency = 0
        self.active = 0
        self.max_active = 0

    def _metadata_response(self, method, url, kwargs):
        if url.endswith("/sites/demo"):
            return Response({"id": "site"})
        if url.endswith("/sites/site/drive"):
            return Response(
                {
                    "id": "drive",
                    "name": "Documents",
                    "webUrl": f"{_DEMO_SITE_URL}/Documents",
                }
            )
        if "/shares/" in url:
            share_id = url.split("/shares/", 1)[1].split("/", 1)[0]
            return Response(self.items[share_id])
        if "/root:/" in url:
            name = unquote(url.rsplit("/", 1)[-1])
            return Response(
                next(item for item in self.items.values() if item.get("name") == name)
            )
        if "/children" in url:
            if method == "POST":
                return Response(
                    {
                        "id": "created-folder",
                        "name": kwargs["json"]["name"],
                        "folder": {},
                        "parentReference": {"driveId": "drive"},
                    }
                )
            children = self.children.get(url, [])
            return Response(
                children if isinstance(children, dict) else {"value": children}
            )
        return None

    async def _transfer_response(self, method, url, kwargs):
        if method == "POST" and url.endswith("createUploadSession"):
            return Response(
                {"uploadUrl": "https://tenant.sharepoint.com/upload/session"}
            )
        if method == "PUT" and url.endswith("/content"):
            data = kwargs.get("content", b"")
            self.uploaded[url] = data
            return Response({"id": "direct", "name": "direct.bin", "size": len(data)})
        if method == "PUT" and "/upload/" in url:
            data = kwargs.get("content", b"")
            self.uploaded[url] = self.uploaded.get(url, b"") + data
            return Response(
                {
                    "id": "uploaded",
                    "name": "uploaded.bin",
                    "size": len(self.uploaded[url]),
                }
            )
        if "/download/" in url:
            await asyncio.sleep(0.02)
            return Response(content=b"payload")
        return Response()

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.request_latency:
                await asyncio.sleep(self.request_latency)
            response = self._metadata_response(method, url, kwargs)
            if response is not None:
                return response
            return await self._transfer_response(method, url, kwargs)
        finally:
            self.active -= 1


class StreamingResponse(Response):
    async def aiter_bytes(self, _chunk_size):
        await asyncio.sleep(0.02)
        yield self.content


class StreamingClient(Client):
    def __init__(self):
        super().__init__()
        self.stream_active = 0
        self.max_stream_active = 0
        self.stream_started = asyncio.Event()

    def stream(self, _method, _url):
        client = self

        class Context:
            async def __aenter__(self):
                client.stream_active += 1
                client.max_stream_active = max(
                    client.max_stream_active, client.stream_active
                )
                client.stream_started.set()
                return StreamingResponse(content=b"payload")

            async def __aexit__(self, exc_type, exc, traceback):
                client.stream_active -= 1

        return Context()


class Provider:
    def get_token(self, scope):
        assert scope == "https://graph.microsoft.com/.default"
        return types.SimpleNamespace(
            token="async-token", expires_on=int(time.time()) + 3600
        )


async def _check_credential_authentication() -> None:
    class ConfidentialClient:
        authority = None

        def __init__(self, client_id, *, client_credential, authority):
            self.client_id = client_id
            self.client_credential = client_credential
            self.authority = authority

        def acquire_token_for_client(self, scopes):
            assert scopes == ["https://graph.microsoft.com/.default"]
            return {"access_token": "credential-token", "expires_in": 3600}

    original_msal = sys.modules["msal"]
    original_to_thread = async_core.asyncio.to_thread

    async def direct_to_thread(function, /, *args, **kwargs):
        await asyncio.sleep(0)
        return function(*args, **kwargs)

    credential_msal = types.ModuleType("msal")
    credential_msal.ConfidentialClientApplication = ConfidentialClient
    credential_msal.PublicClientApplication = ConfidentialClient
    sys.modules["msal"] = credential_msal
    async_core.asyncio.to_thread = direct_to_thread
    try:
        credential_manager = AsyncSharepointManager(
            _DEMO_SITE_URL,
            ClientCredential("client-id", "client-secret"),
            tenant_id="tenant-guid",
            client=Client(),
        )
        assert await credential_manager._ensure_token() == "credential-token"
        assert credential_manager._msal_client.authority.endswith("/tenant-guid")
        try:
            AsyncSharepointManager(
                _DEMO_SITE_URL,
                ClientCredential("client-id", "client-secret"),
                client=Client(),
            )
        except ValueError as exc:
            assert "tenant_id" in str(exc)
        else:
            raise AssertionError("credential auth accepted a missing tenant_id")
    finally:
        sys.modules["msal"] = original_msal
        async_core.asyncio.to_thread = original_to_thread


async def _check_failed_provider() -> None:
    class FailingProvider:
        def get_token(self, _scope):
            return {"error": "invalid_grant", "error_description": "secret-canary"}

    failing_manager = AsyncSharepointManager(
        _DEMO_SITE_URL,
        token_provider=FailingProvider(),
        client=Client(),
    )
    try:
        await failing_manager._ensure_token()
    except SPAuthenticationError as exc:
        assert "secret-canary" not in str(exc)
    else:
        raise AssertionError("failed token response was accepted")


class RetryResponse:
    def __init__(self, status_code, retry_after="10"):
        self.status_code = status_code
        self.headers = {"Retry-After": retry_after}
        self.closed = False

    async def aclose(self):
        await asyncio.sleep(0)
        self.closed = True


async def _check_retries(manager: AsyncSharepointManager) -> None:
    original_policy = manager.policy
    original_request = manager._request
    original_sleep = async_core.asyncio.sleep
    manager.policy = OperationPolicy(
        max_retry_attempts=3,
        max_retry_after_seconds=0.01,
        wall_clock_seconds=1,
    )
    calls = []
    sleeps = []
    responses = [RetryResponse(503), RetryResponse(201)]

    async def fake_request(method, url, **kwargs):
        calls.append((method, kwargs["timeout"]))
        await original_sleep(0)
        return responses.pop(0)

    async def fake_sleep(delay):
        sleeps.append(delay)
        await original_sleep(0)

    manager._request = fake_request
    async_core.asyncio.sleep = fake_sleep
    post = await manager._retry_request("POST", _GRAPH_ITEMS_URL)
    assert post.status_code == 503
    assert len(calls) == 1
    calls.clear()
    transient = RetryResponse(503)
    responses[:] = [transient, RetryResponse(200)]
    put = await manager._retry_request("PUT", _GRAPH_ITEMS_URL)
    assert put.status_code == 200
    assert len(calls) == 2
    assert sleeps and sleeps[0] <= 0.01
    assert transient.closed
    assert responses == []

    responses[:] = [RetryResponse(503, retry_after="0"), RetryResponse(200)]
    calls.clear()
    immediate = await manager._retry_request("PUT", _GRAPH_ITEMS_URL)
    assert immediate.status_code == 200
    assert len(calls) == 2

    manager.policy = OperationPolicy(
        max_retry_attempts=3,
        max_retry_after_seconds=0.01,
        wall_clock_seconds=0.01,
    )

    async def slow_request(method, url, **kwargs):
        await original_sleep(0.02)
        return RetryResponse(503)

    manager._request = slow_request
    started = time.monotonic()
    try:
        await manager._retry_request("PUT", _GRAPH_ITEMS_URL)
    except SPDeadlineExceeded:
        assert time.monotonic() - started < 0.1
    else:
        raise AssertionError("retry deadline was not enforced")

    manager._request = original_request
    manager.policy = original_policy
    async_core.asyncio.sleep = original_sleep


def _upload_requests(fake_client: Client) -> list[tuple[str, str]]:
    return [
        (method, url)
        for method, url in fake_client.requests
        if "createUploadSession" in url or "/upload/" in url or url.endswith("/content")
    ]


async def _check_downloads(
    manager: AsyncSharepointManager,
    client: Client,
    file_payload: dict,
    file_url: str,
    file_url_2: str,
    directory: str,
) -> None:
    first = Path(directory) / "one.bin"
    second = Path(directory) / "two.bin"
    await asyncio.gather(
        manager.download_file_from_url(file_url, str(first)),
        manager.download_file_from_url(file_url_2, str(second)),
    )
    assert first.read_bytes() == b"payload"
    assert second.read_bytes() == b"payload"
    assert client.max_active == 2
    sharing_url = "https://tenant.sharepoint.com/:f:/s/sites/demo/Eabc"
    client.items[manager._share_id(sharing_url)] = file_payload
    sharing_file = Path(directory) / "sharing.bin"
    await manager.download_file_from_url(sharing_url, str(sharing_file))
    assert sharing_file.read_bytes() == b"payload"

    for limit in (1, 2):
        stream_client = StreamingClient()
        stream_client.items[manager._share_id(file_url)] = file_payload
        stream_manager = AsyncSharepointManager(
            _DEMO_SITE_URL,
            token_provider=Provider(),
            policy=OperationPolicy(max_concurrency=limit),
            client=stream_client,
        )
        stream_manager._site_id = "site"
        stream_manager._drive_id = "drive"
        stream_manager._drive_url_name = "Documents"
        await asyncio.gather(
            *(
                stream_manager.download_file_from_url(
                    file_url, str(Path(directory) / f"stream-{limit}-{index}.bin")
                )
                for index in range(3)
            )
        )
        assert stream_client.max_stream_active == limit
        await stream_manager.close()


async def _check_direct_uploads(
    manager: AsyncSharepointManager,
    client: Client,
    folder_url: str,
    directory: str,
) -> None:
    source = Path(directory) / "upload.bin"
    source.write_bytes(b"upload")
    client.requests.clear()
    await manager.upload_file_to_folder_url(folder_url, str(source))
    assert b"upload" in b"".join(client.uploaded.values())
    assert len(_upload_requests(client)) == 1

    threshold = Path(directory) / "threshold.bin"
    threshold.write_bytes(b"x" * _DIRECT_UPLOAD_MAX_BYTES)
    client.requests.clear()
    await manager.upload_file_to_folder_url(folder_url, str(threshold))
    assert len(_upload_requests(client)) == 1

    large = Path(directory) / "large.bin"
    large.write_bytes(b"x" * (_DIRECT_UPLOAD_MAX_BYTES + 1))
    client.requests.clear()
    await manager.upload_file_to_folder_url(folder_url, str(large))
    assert len(_upload_requests(client)) == 3


async def _check_workload(folder_url: str, folder_payload: dict) -> None:
    workload_client = Client()
    workload_client.request_latency = 0.001
    workload_manager = AsyncSharepointManager(
        _DEMO_SITE_URL,
        token_provider=Provider(),
        policy=OperationPolicy(max_concurrency=100),
        client=workload_client,
    )
    workload_manager._site_id = "site"
    workload_manager._drive_id = "drive"
    workload_manager._drive_url_name = "Documents"
    workload_client.items[workload_manager._share_id(folder_url)] = folder_payload
    with tempfile.TemporaryDirectory() as workload_directory:
        workload_files = []
        for index in range(100):
            workload_file = Path(workload_directory) / f"workload-{index}.bin"
            workload_file.write_bytes(b"x")
            workload_files.append(workload_file)
        await asyncio.gather(
            *(
                workload_manager.upload_file_to_folder_url(
                    folder_url, str(workload_file)
                )
                for workload_file in workload_files
            )
        )
    assert len(_upload_requests(workload_client)) == 100
    assert len(workload_client.requests) == 200
    assert 200 * workload_client.request_latency < 300 * workload_client.request_latency
    await workload_manager.close()


async def _check_folder_downloads(
    manager: AsyncSharepointManager,
    client: Client,
    folder_url: str,
    foreign_url: str,
    file_payload: dict,
    directory: str,
) -> None:
    await manager.download_folder_from_url(folder_url, directory)
    assert (Path(directory) / "folder" / "remote.bin").read_bytes() == b"payload"
    assert (Path(directory) / "folder" / "remote-2.bin").read_bytes() == b"payload"

    first_children_url = (
        "https://graph.microsoft.com/v1.0/drives/drive/items/folder/children"
    )
    saved_children = client.children[first_children_url]
    client.children[first_children_url] = {
        "value": [],
        "@odata.nextLink": first_children_url,
    }
    try:
        await manager.download_folder_from_url(folder_url, directory)
    except SPValidationError:
        pass
    else:
        raise AssertionError("repeated async pagination link accepted")
    client.children[first_children_url] = saved_children

    saved_policy = manager.policy
    manager.policy = OperationPolicy(max_concurrency=2, max_pages=1)
    try:
        await manager.download_folder_from_url(folder_url, directory)
    except SPValidationError:
        pass
    else:
        raise AssertionError("async pagination page budget was ignored")
    manager.policy = saved_policy

    try:
        await manager.download_file_from_url(
            foreign_url, str(Path(directory) / "foreign.bin")
        )
    except SPUnauthorizedTarget:
        pass
    else:
        raise AssertionError("async cross-drive item was accepted")

    oversized_url = f"{_DEMO_SITE_URL}/Documents/oversized.bin"
    client.items[manager._share_id(oversized_url)] = {
        **file_payload,
        "id": "oversized",
        "name": "oversized.bin",
        "size": 3,
    }
    oversized = Path(directory) / "oversized.bin"
    try:
        await manager.download_file_from_url(oversized_url, str(oversized))
    except SPValidationError:
        pass
    else:
        raise AssertionError("async oversized response accepted")
    assert not oversized.exists()


async def _check_stream_cleanup(
    manager: AsyncSharepointManager,
    client: Client,
    file_url: str,
    folder_url: str,
    directory: str,
) -> None:
    descriptor_count = len(os.listdir("/proc/self/fd"))

    def fail_stream(method, url):
        raise RuntimeError("stream setup failed")

    client.stream = fail_stream
    setup_failure = Path(directory) / "setup-failure.bin"
    try:
        await manager.download_file_from_url(file_url, str(setup_failure))
    except SPGraphError as exc:
        assert "stream setup failed" not in str(exc)
    else:
        raise AssertionError("stream setup failure was swallowed")
    finally:
        del client.stream
    assert not setup_failure.exists()
    assert not list(Path(directory).glob(".sp-async-download-*"))
    assert len(os.listdir("/proc/self/fd")) <= descriptor_count

    local_folder = Path(directory) / "local-folder"
    local_folder.mkdir()
    (local_folder / "nested.bin").write_bytes(b"nested")
    await manager.upload_folder_to_folder_url(folder_url, str(local_folder))
    assert b"nested" in b"".join(client.uploaded.values())


async def _check_cancellation(
    manager: AsyncSharepointManager,
    client: Client,
    file_url: str,
    directory: str,
) -> None:
    cancelled = Path(directory) / "cancelled.bin"
    try:
        cancellation_client = StreamingClient()
        client.stream = cancellation_client.stream
        task = asyncio.create_task(
            manager.download_file_from_url(file_url, str(cancelled))
        )
        await cancellation_client.stream_started.wait()
        task.cancel()
        await task
    finally:
        del client.stream
        assert not cancelled.exists()
        assert not list(Path(directory).glob(".sp-async-download-*"))


async def _check_file_operations(
    manager: AsyncSharepointManager,
    client: Client,
    file_payload: dict,
    folder_payload: dict,
    file_url: str,
    file_url_2: str,
    folder_url: str,
    foreign_url: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        await _check_downloads(
            manager, client, file_payload, file_url, file_url_2, directory
        )
        await _check_direct_uploads(manager, client, folder_url, directory)
        await _check_workload(folder_url, folder_payload)
        await _check_folder_downloads(
            manager, client, folder_url, foreign_url, file_payload, directory
        )
        await _check_stream_cleanup(manager, client, file_url, folder_url, directory)
        with suppress(asyncio.CancelledError):
            await _check_cancellation(manager, client, file_url, directory)


async def main() -> None:
    await _check_credential_authentication()
    await _check_failed_provider()

    client = Client()
    manager = AsyncSharepointManager(
        _DEMO_SITE_URL,
        token_provider=Provider(),
        policy=OperationPolicy(max_concurrency=2),
        client=client,
    )
    file_payload = {
        "id": "file",
        "name": "remote.bin",
        "size": 7,
        "file": {},
        "@microsoft.graph.downloadUrl": "https://tenant.sharepoint.com/download/file",
        "parentReference": {"driveId": "drive"},
    }
    file_payload_2 = {**file_payload, "id": "file-2", "name": "remote-2.bin"}
    folder_payload = {
        "id": "folder",
        "name": "folder",
        "folder": {},
        "parentReference": {"driveId": "drive"},
    }
    file_url = f"{_DEMO_SITE_URL}/Documents/remote.bin"
    file_url_2 = f"{_DEMO_SITE_URL}/Documents/remote-2.bin"
    folder_url = f"{_DEMO_SITE_URL}/Documents/folder"
    client.items[manager._share_id(file_url)] = file_payload
    client.items[manager._share_id(file_url_2)] = file_payload_2
    client.items[manager._share_id(folder_url)] = folder_payload
    client.children[
        "https://graph.microsoft.com/v1.0/drives/drive/items/folder/children"
    ] = {
        "value": [file_payload],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/drive/items/folder/children?page=2",
    }
    client.children[
        "https://graph.microsoft.com/v1.0/drives/drive/items/folder/children?page=2"
    ] = {"value": [file_payload_2]}

    await _check_retries(manager)

    foreign_url = f"{_DEMO_SITE_URL}/Other/foreign.bin"
    client.items[manager._share_id(foreign_url)] = {
        **file_payload,
        "id": "foreign",
        "parentReference": {"siteId": "site", "driveId": "foreign-drive"},
    }
    await _check_file_operations(
        manager,
        client,
        file_payload,
        folder_payload,
        file_url,
        file_url_2,
        folder_url,
        foreign_url,
    )
    await manager.close()


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        asyncio.run(main())
