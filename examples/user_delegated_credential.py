"""Offline user-delegated credential setup.

Username/password (ROPC) authentication is a legacy service-account flow.
Use an interactive delegated flow for new applications.
"""

import os
from unittest.mock import Mock

from sharepoint_manager import SharepointManager, UserDelegatedCredential


def main() -> None:
    """Create legacy delegated credentials without making a network request."""
    credential = UserDelegatedCredential(
        os.environ.get("SP_CLIENT_ID", "offline-client"),
        os.environ.get("SP_USERNAME", "offline-user@example.test"),
        os.environ.get("SP_PASSWORD") or "offline-value",
    )
    manager = Mock(spec=SharepointManager)
    manager.get_folder_metadata_from_url(
        "https://tenant.sharepoint.com/sites/demo/Documents"
    )
    assert credential.username
    print("offline user-delegated example complete")


if __name__ == "__main__":
    main()
