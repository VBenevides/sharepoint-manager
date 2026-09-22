import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager


class Response:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        return None

    def close(self):
        return None


def main() -> None:
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.policy = types.SimpleNamespace(
        max_pages=10, max_items=10, wall_clock_seconds=60
    )
    manager._hdr = lambda: {"Authorization": "Bearer token"}
    pages = {
        "https://graph.microsoft.com/v1.0/delta": Response(
            {
                "value": [{"id": "f1", "name": "new.txt", "file": {}}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/delta?p=2",
            }
        ),
        "https://graph.microsoft.com/v1.0/delta?p=2": Response(
            {
                "value": [
                    {"id": "gone", "deleted": {"state": "deleted"}, "name": "old.txt"}
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=done",
            }
        ),
    }
    manager._request = lambda method, url, **kwargs: pages[url]
    result = list(
        manager.iter_folder_delta(delta_link="https://graph.microsoft.com/v1.0/delta")
    )
    assert result[-1].delta_link.endswith("token=done")
    assert [item.id for item in result[0].files] == ["f1"]
    assert not result[0].folders
    assert [(item.id, item.metadata["deleted"]) for item in result[1].deleted] == [
        ("gone", {"state": "deleted"})
    ]

    manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    manager._get_drive_item_from_url = lambda url: {
        "id": "folder-a",
        "folder": {},
        "parentReference": {"driveId": "drive-a"},
    }
    folder_delta = manager._graph_base_url + "/drives/drive-a/items/folder-a/delta"
    folder_delta_page = folder_delta + "?page=2"
    pages[folder_delta] = Response(
        {
            "value": [{"id": "folder-file", "name": "a.txt", "file": {}}],
            "@odata.nextLink": folder_delta_page,
        }
    )
    pages[folder_delta_page] = Response(
        {
            "value": [{"id": "subfolder", "name": "Sub", "folder": {}}],
            "@odata.deltaLink": folder_delta + "?token=folder-done",
        }
    )
    manager._request = lambda method, url, **kwargs: pages[url]
    delta_link, files, folders, deleted = manager.get_folder_delta_from_url(
        "https://tenant.sharepoint.com/sites/demo/Documents/Folder"
    )
    assert delta_link.endswith("token=folder-done")
    assert [item.id for item in files] == ["folder-file"]
    assert [item.id for item in folders] == ["subfolder"]
    assert not deleted

    def fail_url_resolution(url):
        raise AssertionError(f"URL should be ignored: {url}")

    manager._get_drive_item_from_url = fail_url_resolution
    delta_link, files, folders, deleted = manager.get_folder_delta_from_url(
        "ignored", delta_link="https://graph.microsoft.com/v1.0/delta"
    )
    assert delta_link.endswith("token=done")
    assert [item.id for item in files] == ["f1"]
    assert not folders and len(deleted) == 1


if __name__ == "__main__":
    main()
