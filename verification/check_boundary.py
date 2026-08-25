import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager, SharepointManagerBase
from sharepoint_manager.dataclasses import SPFile, SPFolder
from sharepoint_manager.exceptions import SPUnauthorizedTarget


def main() -> None:
    manager = object.__new__(SharepointManagerBase)
    manager._site_id = "tenant.sharepoint.com,site-a,web-a"
    manager._drive_id = "drive-a"
    allowed = {"parentReference": {"siteId": "site-a", "driveId": "drive-a"}}
    manager._validate_item_boundary(allowed)
    manager._validate_item_boundary(
        {"parentReference": {"siteId": "web-a", "driveId": "drive-a"}}
    )

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

    download_manager = object.__new__(SharepointManager)
    download_manager._site_id = "site-a"
    download_manager._drive_id = "drive-a"
    download_manager._check_depth = lambda path: None
    download_manager._check_file_budget = lambda *args, **kwargs: None
    foreign_file = SPFile(
        id="file-b",
        name="foreign.txt",
        parent_reference={"siteId": "site-b", "driveId": "drive-a"},
    )
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "download"
        try:
            download_manager.download_file(foreign_file, str(target))
        except SPUnauthorizedTarget:
            pass
        else:
            raise AssertionError("foreign SPFile accepted by download_file")
        assert not target.exists()


if __name__ == "__main__":
    main()
