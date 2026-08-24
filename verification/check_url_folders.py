import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.exceptions import SPFolderNotEmpty, SPUnauthorizedTarget, SPValidationError


class Response:
    status_code = 200
    headers = {}

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
    manager.policy = types.SimpleNamespace(max_pages=3, max_items=10, wall_clock_seconds=60)
    manager._hdr = lambda json_content=False: {"Authorization": "Bearer token"}
    share_url = "https://tenant.sharepoint.com/sites/site/Shared%20Documents/Folder%20%231"
    folder = {
        "id": "folder-a",
        "name": "Folder #1",
        "folder": {"childCount": 1},
        "parentReference": {"siteId": "site-a", "driveId": "drive-a"},
    }
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if "/shares/" in url:
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
                            "grantedToV2": {"user": {"id": "user-a", "displayName": "A User"}},
                        }
                    ]
                }
            )
        raise AssertionError((method, url))

    manager._request = request
    metadata = manager.get_folder_metadata_from_url(share_url)
    assert metadata.id == "folder-a"
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
        if "/shares/" in url
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
        if "/shares/" in url:
            return Response(folder)
        if url.endswith("/children"):
            return Response({"value": []})
        deleted.append((method, url))
        return Response({})

    manager._request = delete_request
    manager.delete_folder_from_url(share_url)
    assert deleted and deleted[0][0] == "DELETE"

    manager._request = lambda method, url, **kwargs: Response(
        {**folder, "parentReference": {"siteId": "site-b", "driveId": "drive-a"}}
    )
    try:
        manager.get_folder_metadata_from_url(share_url)
    except SPUnauthorizedTarget:
        pass
    else:
        raise AssertionError("off-boundary folder accepted")

    assert len(calls) >= 6


if __name__ == "__main__":
    main()
