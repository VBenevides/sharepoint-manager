import sys
import types
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager


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
    first = "https://graph.microsoft.com/v1.0/items"
    second = first + "?p=2"
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.policy = types.SimpleNamespace(
        max_pages=3, max_items=5, wall_clock_seconds=60
    )
    manager._hdr = lambda: {"Authorization": "Bearer token"}
    requested = []
    pages = {
        first: Response({"value": [{"id": "one"}], "@odata.nextLink": second}),
        second: Response({"value": [{"id": "two"}]}),
    }
    manager._request = lambda method, url, **kwargs: requested.append(url) or pages[url]
    iterator = manager.iter_collection(first)
    first_page = next(iterator)
    assert requested == [first]
    result = [first_page, next(iterator)]
    assert [item["id"] for page in result for item in page.items] == ["one", "two"]
    assert result[0].next_link == second and result[1].next_link is None

    delta = "https://graph.microsoft.com/v1.0/delta"
    manager._request = lambda method, url, **kwargs: Response(
        {"value": [{"id": "gone", "deleted": {}}], "@odata.deltaLink": delta + "?done"}
    )
    page = next(manager.iter_folder_delta(delta_link=delta))
    assert page.deleted[0].id == "gone"
    assert page.delta_link.endswith("?done")


if __name__ == "__main__":
    main()
