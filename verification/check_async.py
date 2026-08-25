import asyncio
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

from sharepoint_manager import AsyncSharepointManager, OperationPolicy
from sharepoint_manager.exceptions import SPUnauthorizedTarget


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
            if url.endswith("/children"):
                if method == "POST":
                    return Response(
                        {
                            "id": "created-folder",
                            "name": kwargs["json"]["name"],
                            "folder": {},
                            "parentReference": {"driveId": "drive"},
                        }
                    )
                return Response({"value": self.children.get(url, [])})
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
    ] = [file_payload]

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

        try:
            await manager.download_file_from_url(
                foreign_url, str(Path(directory) / "foreign.bin")
            )
        except SPUnauthorizedTarget:
            pass
        else:
            raise AssertionError("async cross-drive item was accepted")

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
