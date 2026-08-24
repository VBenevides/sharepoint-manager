import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager  # noqa: E402
from sharepoint_manager.dataclasses import SPFolder  # noqa: E402


class Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"id": "folder", "name": "São #1"}


def main() -> None:
    folder = SPFolder(
        id="folder",
        name="São #1",
        parent_reference={"path": "/drives/drive/root:/Archive%20Files"},
    )
    assert folder.relative_url == "Archive Files/São #1"

    manager = object.__new__(SharepointManager)
    manager._site_id = "site"
    manager._drive_id = "drive"
    manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    manager._hdr = lambda: {"Authorization": "Bearer token"}
    seen = []
    manager._request = lambda method, url, **kwargs: seen.append(url) or Response()
    manager._get_folder("Folder #1/100%/São")
    assert "%23" in seen[0] and "%25" in seen[0] and "%C3%A3" in seen[0]


if __name__ == "__main__":
    main()
