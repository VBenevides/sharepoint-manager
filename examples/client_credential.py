"""Offline client-credential setup.

The mock manager keeps this example safe to run without contacting SharePoint.
"""

import os
from unittest.mock import Mock

from sharepoint_manager import ClientCredential, SharepointManager


def main() -> None:
    """Create application credentials and call a public manager method."""
    credential = ClientCredential(
        os.environ.get("SP_CLIENT_ID", "offline-client"),
        os.environ.get("SP_CLIENT_SECRET") or "offline-value",
    )
    manager = Mock(spec=SharepointManager)
    manager.list_folder_from_url("https://tenant.sharepoint.com/sites/demo/Documents")
    assert credential.client_id
    print("offline client-credential example complete")


if __name__ == "__main__":
    main()
