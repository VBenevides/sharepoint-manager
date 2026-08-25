import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.dataclasses import SPFile
from sharepoint_manager.exceptions import SPAmbiguousWriteError
from sharepoint_manager.utils import safe_join


def main() -> None:
    canary = "https://upload.sharepoint.com/session?secret=capability-sentinel"
    file = SPFile.from_dict(
        {
            "id": "file",
            "name": "file.txt",
            "@microsoft.graph.downloadUrl": canary,
        }
    )
    assert canary not in repr(file)
    assert canary not in repr([file])
    error = SPAmbiguousWriteError(canary)
    assert canary not in repr(error) and canary not in str(error)
    assert error.upload_url == canary

    source = (Path(__file__).parents[1] / "sharepoint_manager/core.py").read_text()
    for expression in (
        'logger.info("Uploading file %s',
        'logger.info("Download completed: %s',
    ):
        assert expression not in source
    try:
        safe_join("/tmp", "")
    except ValueError as exc:
        assert "secret-sentinel" not in str(exc)


if __name__ == "__main__":
    main()
