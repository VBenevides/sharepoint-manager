import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.dataclasses import OperationPolicy
from sharepoint_manager.utils import validate_archive_members


def main() -> None:
    policy = OperationPolicy(max_file_bytes=10, max_total_bytes=20, max_disk_bytes=20)
    assert policy.max_pages == 1000
    for kwargs in (
        {"max_pages": 0},
        {"wall_clock_seconds": float("inf")},
        {"max_file_bytes": 21, "max_total_bytes": 20},
    ):
        try:
            OperationPolicy(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(kwargs)
    validate_archive_members([("folder/file", 4)], 2, 4)
    try:
        validate_archive_members([("../secret", 1)], 2, 4)
    except ValueError:
        pass
    else:
        raise AssertionError("archive traversal accepted")


if __name__ == "__main__":
    main()
