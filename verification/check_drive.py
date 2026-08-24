import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager  # noqa: E402
from sharepoint_manager.exceptions import SPDriveNotFound  # noqa: E402


def main() -> None:
    manager = object.__new__(SharepointManager)
    manager._site_id = "site-a"
    manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    manager.document_folder_name = "Archive"
    manager._paginate = lambda url: iter(
        [{"name": "Documents", "id": "drive-a"}, {"name": "Archive", "id": "drive-b"}]
    )
    assert manager._get_drive_id() == "drive-b"
    manager.document_folder_name = "Missing"
    try:
        manager._get_drive_id()
    except SPDriveNotFound:
        pass
    else:
        raise AssertionError("missing explicit drive was accepted")


if __name__ == "__main__":
    main()
