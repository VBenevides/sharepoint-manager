import sys
import tempfile
import types
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import SPFile, SPFolder
from sharepoint_manager.exceptions import (
    SPFolderNotEmpty,
    SPUnauthorizedTarget,
    SPValidationError,
)


class Response:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body

    def close(self):
        return None


def main() -> None:
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    manager._site_id = "site-a"
    manager._drive_id = "drive-a"
    manager.url = "https://tenant.sharepoint.com/sites/site"
    manager._drive_url_name = "Shared Documents"
    manager.policy = types.SimpleNamespace(
        max_pages=3, max_items=10, wall_clock_seconds=60
    )
    manager._hdr = lambda json_content=False: {"Authorization": "Bearer token"}
    share_url = (
        "https://tenant.sharepoint.com/sites/site/Shared%20Documents/Folder%20%231"
    )
    folder = {
        "id": "folder-a",
        "name": "Folder #1",
        "folder": {"childCount": 1},
        "parentReference": {"siteId": "site-a", "driveId": "drive-a"},
    }
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if "/shares/" in url or "/drives/drive-a/root:/" in url:
            return Response(folder)
        if method == "GET" and url.endswith("/children"):
            next_url = manager._graph_base_url + "/v1.0/children?p=2"
            return Response(
                {
                    "value": [{"id": "file-a", "name": "a.txt", "file": {}}],
                    "@odata.nextLink": next_url,
                }
            )
        if url.endswith("/children?p=2"):
            return Response({"value": [{"id": "sub-a", "name": "Sub", "folder": {}}]})
        if method == "POST" and url.endswith("/children"):
            return Response(
                {
                    "id": "new-folder",
                    "name": kwargs["json"]["name"],
                    "folder": {},
                    "parentReference": {"siteId": "site-a", "driveId": "drive-a"},
                }
            )
        if url.endswith("/permissions"):
            return Response(
                {
                    "value": [
                        {
                            "id": "permission-a",
                            "roles": ["read"],
                            "grantedToV2": {
                                "user": {"id": "user-a", "displayName": "A User"}
                            },
                        }
                    ]
                }
            )
        raise AssertionError((method, url))

    manager._request = request
    metadata = manager.get_folder_metadata_from_url(share_url)
    assert metadata.id == "folder-a"
    sharing_url = "https://tenant.sharepoint.com/:f:/s/sites/site/Eabc"
    assert manager.get_folder_metadata_from_url(sharing_url).id == "folder-a"
    redirect_url = "https://tenant.sharepoint.com/:f:/r/sites/site/Shared%20Documents/Folder%20%231"
    assert manager.get_folder_metadata_from_url(redirect_url).id == "folder-a"
    assert any("/drives/drive-a/root:/Folder%20%231" in url for _, url, _ in calls)
    files, folders = manager.list_folder_from_url(share_url)
    assert set(files) == {"a.txt"} and set(folders) == {"Sub"}
    created = manager.create_folder_from_url(share_url, "New #1")
    assert created.name == "New #1"
    permissions = manager.get_folder_permissions_from_url(share_url)
    assert permissions[0]["granted_to"]["id"] == "user-a"
    assert permissions[0]["roles"] == ("read",)

    try:
        manager.create_folder_from_url(share_url, "bad/name")
    except SPValidationError:
        pass
    else:
        raise AssertionError("unsafe folder name accepted")

    manager._request = lambda method, url, **kwargs: (
        Response(folder)
        if "/shares/" in url or "/drives/drive-a/root:/" in url
        else Response({"value": [{"id": "file-a", "name": "a.txt", "file": {}}]})
    )
    try:
        manager.delete_folder_from_url(share_url)
    except SPFolderNotEmpty:
        pass
    else:
        raise AssertionError("non-empty folder deleted")

    deleted = []

    def delete_request(method, url, **kwargs):
        if "/shares/" in url or "/drives/drive-a/root:/" in url:
            return Response(folder)
        if url.endswith("/children"):
            return Response({"value": []})
        deleted.append((method, url))
        return Response({})

    manager._request = delete_request
    manager.delete_folder_from_url(share_url)
    assert deleted and deleted[0][0] == "DELETE"

    child_lists = []

    def force_delete_request(method, url, **kwargs):
        if "/shares/" in url or "/drives/drive-a/root:/" in url:
            return Response(folder)
        if url.endswith("/children"):
            child_lists.append(url)
            return Response({"value": [{"id": "should-not-be-read"}]})
        deleted.append((method, url))
        return Response({})

    manager._request = force_delete_request
    manager.delete_folder_from_url(share_url, force_delete=True)
    assert not child_lists

    manager._request = lambda method, url, **kwargs: Response(
        {**folder, "parentReference": {"siteId": "site-b", "driveId": "drive-a"}}
    )
    try:
        manager.get_folder_metadata_from_url(share_url)
    except SPUnauthorizedTarget:
        pass
    else:
        raise AssertionError("off-boundary folder accepted")

    file_obj = SPFile(
        id="file-a",
        name="a.txt",
        parent_reference={"siteId": "site-a", "driveId": "drive-a"},
    )
    manager.get_file_metadata_from_url = lambda url: file_obj
    manager._request = lambda method, url, **kwargs: Response(
        {
            "value": [
                {
                    "id": "permission-a",
                    "roles": ["read"],
                    "grantedToV2": {"user": {"id": "user-a", "displayName": "A User"}},
                }
            ]
        }
    )
    file_permissions = manager.get_file_permissions_from_url(share_url)
    assert file_permissions[0]["granted_to"]["id"] == "user-a"

    transfer_calls = []
    manager.get_folder_metadata_from_url = lambda url: SPFolder(
        id="folder-a",
        name="Folder #1",
        parent_reference={"siteId": "site-a", "driveId": "drive-a"},
    )
    manager.upload_file = lambda path, **kwargs: (
        transfer_calls.append(("upload_file", path, kwargs)) or file_obj
    )
    manager.upload_folder = lambda path, **kwargs: transfer_calls.append(
        ("upload_folder", path, kwargs)
    )
    manager.download_folder = lambda path, **kwargs: transfer_calls.append(
        ("download_folder", path, kwargs)
    )
    with tempfile.TemporaryDirectory() as directory:
        local_file = Path(directory) / "a.txt"
        local_file.write_bytes(b"data")
        local_folder = Path(directory) / "tree"
        local_folder.mkdir()
        assert manager.upload_file_to_folder_url(share_url, str(local_file)) is file_obj
        manager.upload_folder_to_folder_url(share_url, str(local_folder))
        manager.download_folder_from_url(share_url, str(Path(directory) / "out"))
    assert [call[0] for call in transfer_calls] == [
        "upload_file",
        "upload_folder",
        "download_folder",
    ]
    assert all(call[2]["_folder"].id == "folder-a" for call in transfer_calls)

    assert len(calls) >= 6


if __name__ == "__main__":
    main()
