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


def main() -> None:
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.policy = types.SimpleNamespace(max_pages=10, max_items=10)
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
    checkpoint, files, folders, deleted = manager._consume_delta(
        "https://graph.microsoft.com/v1.0/delta"
    )
    assert checkpoint.endswith("token=done")
    assert [item.id for item in files] == ["f1"]
    assert not folders
    assert [(item.id, item.metadata["deleted"]) for item in deleted] == [
        ("gone", {"state": "deleted"})
    ]


if __name__ == "__main__":
    main()
