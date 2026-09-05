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
from sharepoint_manager.dataclasses import OperationPolicy
from sharepoint_manager.exceptions import SPValidationError

_GRAPH_NEXT_LINK = "@odata.nextLink"


class Response:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        return None

    def close(self):
        return None


def manager(pages, policy=None):
    obj = object.__new__(SharepointManager)
    obj.graph_host = "graph.microsoft.com"
    obj.tenant_url = "https://tenant.sharepoint.com"
    obj.policy = policy or OperationPolicy(max_pages=3, max_items=3)
    obj._hdr = lambda: {"Authorization": "Bearer token"}
    obj._request = lambda method, url, **kwargs: pages[url]
    return obj


def main() -> None:
    first = "https://graph.microsoft.com/v1.0/items"
    second = first + "?page=2"
    obj = manager(
        {
            first: Response({"value": [{"id": "1"}], _GRAPH_NEXT_LINK: second}),
            second: Response({"value": [{"id": "2"}]}),
        }
    )
    assert [item["id"] for item in obj._paginate(first)] == ["1", "2"]

    obj = manager({first: Response({"value": [], _GRAPH_NEXT_LINK: first})})
    try:
        list(obj._paginate(first))
    except SPValidationError:
        pass
    else:
        raise AssertionError("repeated pagination link accepted")

    obj = manager(
        {first: Response({"value": [], _GRAPH_NEXT_LINK: "https://evil.example"})}
    )
    try:
        list(obj._paginate(first))
    except SPValidationError:
        pass
    else:
        raise AssertionError("off-host pagination link accepted")


if __name__ == "__main__":
    main()
