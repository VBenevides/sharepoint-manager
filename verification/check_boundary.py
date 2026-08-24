import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManagerBase  # noqa: E402
from sharepoint_manager.dataclasses import SPFile, SPFolder  # noqa: E402
from sharepoint_manager.exceptions import SPUnauthorizedTarget  # noqa: E402


def main() -> None:
    manager = object.__new__(SharepointManagerBase)
    manager._site_id = "site-a"
    manager._drive_id = "drive-a"
    allowed = {"parentReference": {"siteId": "site-a", "driveId": "drive-a"}}
    manager._validate_item_boundary(allowed)

    for item in (
        {"parentReference": {"siteId": "site-b", "driveId": "drive-a"}},
        {"parentReference": {"siteId": "site-a", "driveId": "drive-b"}},
        {},
    ):
        try:
            manager._validate_item_boundary(item)
        except SPUnauthorizedTarget:
            pass
        else:
            raise AssertionError(item)

    manager._validate_file_boundary(
        SPFile(id="file-a", parent_reference={"driveId": "drive-a"})
    )
    manager._validate_object_boundary(
        SPFolder(id="folder-a", parent_reference={"driveId": "drive-a"})
    )


if __name__ == "__main__":
    main()
