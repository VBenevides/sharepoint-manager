import asyncio
import os
import sys
import tempfile
import time
import types
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager import AsyncSharepointManager, OperationPolicy, async_core
from sharepoint_manager.exceptions import (
    SPDeadlineExceeded,
    SPUnauthorizedTarget,
    SPValidationError,
)


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
        self.active = 0
        self.max_active = 0

    async def request(self, method, url, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if "/shares/" in url:
                share_id = url.split("/shares/", 1)[1].split("/", 1)[0]
                return Response(self.items[share_id])
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
            if method == "POST" and url.endswith("createUploadSession"):
                upload_url = "https://tenant.sharepoint.com/upload/session"
                return Response({"uploadUrl": upload_url})
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
        finally:
            self.active -= 1


class Provider:
    def get_token(self, scope):
        assert scope == "https://graph.microsoft.com/.default"
        return types.SimpleNamespace(
            token="async-token", expires_on=int(time.time()) + 3600
        )


async def main() -> None:
    client = Client()
    manager = AsyncSharepointManager(
        "https://tenant.sharepoint.com/sites/demo",
        token_provider=Provider(),
        policy=OperationPolicy(max_concurrency=2),
        client=client,
    )
    manager._site_id = "site"
    manager._drive_id = "drive"
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
    file_url = "https://tenant.sharepoint.com/sites/demo/Documents/remote.bin"
    file_url_2 = "https://tenant.sharepoint.com/sites/demo/Documents/remote-2.bin"
    folder_url = "https://tenant.sharepoint.com/sites/demo/Documents/folder"
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

    class RetryResponse:
        def __init__(self, status_code, retry_after="10"):
            self.status_code = status_code
            self.headers = {"Retry-After": retry_after}
            self.closed = False

        async def aclose(self):
            self.closed = True

    retry_manager = manager
    original_policy = retry_manager.policy
    retry_manager.policy = OperationPolicy(
        max_retry_attempts=3,
        max_retry_after_seconds=0.01,
        wall_clock_seconds=1,
    )
    original_request = retry_manager._request
    original_sleep = async_core.asyncio.sleep
    calls = []
    sleeps = []
    responses = [RetryResponse(503), RetryResponse(201)]

    async def fake_request(method, url, **kwargs):
        calls.append((method, kwargs["timeout"]))
        return responses.pop(0)

    async def fake_sleep(delay):
        sleeps.append(delay)

    retry_manager._request = fake_request
    async_core.asyncio.sleep = fake_sleep
    post = await retry_manager._retry_request(
        "POST", "https://graph.microsoft.com/v1.0/items"
    )
    assert post.status_code == 503
    assert len(calls) == 1
    calls.clear()
    transient = RetryResponse(503)
    responses[:] = [transient, RetryResponse(200)]
    put = await retry_manager._retry_request(
        "PUT", "https://graph.microsoft.com/v1.0/items"
    )
    assert put.status_code == 200
    assert len(calls) == 2
    assert sleeps and sleeps[0] <= 0.01
    assert transient.closed
    assert responses == []

    responses[:] = [RetryResponse(503, retry_after="0"), RetryResponse(200)]
    calls.clear()
    immediate = await retry_manager._retry_request(
        "PUT", "https://graph.microsoft.com/v1.0/items"
    )
    assert immediate.status_code == 200
    assert len(calls) == 2

    retry_manager.policy = OperationPolicy(
        max_retry_attempts=3,
        max_retry_after_seconds=0.01,
        wall_clock_seconds=0.01,
    )

    async def slow_request(method, url, **kwargs):
        await original_sleep(0.02)
        return RetryResponse(503)

    retry_manager._request = slow_request
    started = time.monotonic()
    try:
        await retry_manager._retry_request(
            "PUT", "https://graph.microsoft.com/v1.0/items"
        )
    except SPDeadlineExceeded:
        assert time.monotonic() - started < 0.1
    else:
        raise AssertionError("retry deadline was not enforced")

    retry_manager._request = original_request
    retry_manager.policy = original_policy
    async_core.asyncio.sleep = original_sleep

    foreign_url = "https://tenant.sharepoint.com/sites/demo/Other/foreign.bin"
    client.items[manager._share_id(foreign_url)] = {
        **file_payload,
        "id": "foreign",
        "parentReference": {"siteId": "site", "driveId": "foreign-drive"},
    }

    with tempfile.TemporaryDirectory() as directory:
        first = Path(directory) / "one.bin"
        second = Path(directory) / "two.bin"
        await asyncio.gather(
            manager.download_file_from_url(file_url, str(first)),
            manager.download_file_from_url(file_url_2, str(second)),
        )
        assert first.read_bytes() == b"payload"
        assert second.read_bytes() == b"payload"
        assert client.max_active == 2

        source = Path(directory) / "upload.bin"
        source.write_bytes(b"upload")
        await manager.upload_file_to_folder_url(folder_url, str(source))
        assert b"upload" in b"".join(client.uploaded.values())

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

        oversized_url = "https://tenant.sharepoint.com/sites/demo/Other/oversized.bin"
        client.items[manager._share_id(oversized_url)] = {
            **file_payload,
            "id": "oversized",
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

        descriptor_count = len(os.listdir("/proc/self/fd"))

        def fail_stream(method, url):
            raise RuntimeError("stream setup failed")

        client.stream = fail_stream
        setup_failure = Path(directory) / "setup-failure.bin"
        try:
            await manager.download_file_from_url(file_url, str(setup_failure))
        except RuntimeError:
            pass
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

        cancelled = Path(directory) / "cancelled.bin"
        task = asyncio.create_task(
            manager.download_file_from_url(file_url, str(cancelled))
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not cancelled.exists()
        assert not list(Path(directory).glob(".sp-async-download-*"))

    await manager.close()


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        asyncio.run(main())
