"""Request-count benchmark for the small-file upload decision."""

import sys
import types
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import _DIRECT_UPLOAD_MAX_BYTES, _UPLOAD_CHUNK_SIZE


def session_requests(size: int) -> int:
    return 1 + max(1, ceil(size / _UPLOAD_CHUNK_SIZE))


def main() -> None:
    sizes = (1024, 1024**2, 64 * 1024**2)
    for size in sizes:
        direct = 1
        resumable = session_requests(size)
        selected = direct if size <= _DIRECT_UPLOAD_MAX_BYTES else resumable
        print(
            f"{size:>10} bytes: direct={direct} session={resumable} selected={selected}"
        )
        assert direct < resumable
        assert selected == (direct if size <= _DIRECT_UPLOAD_MAX_BYTES else resumable)

    assert 100 < 100 * session_requests(1024)
    print("100 small files: direct=100 session=200")


if __name__ == "__main__":
    main()
