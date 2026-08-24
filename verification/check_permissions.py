import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.exceptions import SPUnauthorizedTarget


def main() -> None:
    manager = object.__new__(SharepointManager)
    manager._site_id = "site-a"
    manager._drive_id = "drive-a"
    assert manager.validate_resource_scope("site-a", "drive-a")
    for site_id, drive_id in (("site-b", "drive-a"), ("site-a", "drive-b")):
        try:
            manager.validate_resource_scope(site_id, drive_id)
        except SPUnauthorizedTarget:
            pass
        else:
            raise AssertionError((site_id, drive_id))


if __name__ == "__main__":
    main()
