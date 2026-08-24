from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.utils import safe_join  # noqa: E402


def main() -> None:
    source = (Path(__file__).parents[1] / "sharepoint_manager/core.py").read_text()
    for expression in ("logger.info(\"Uploading file %s", "logger.info(\"Download completed: %s"):
        assert expression not in source
    try:
        safe_join("/tmp", "")
    except ValueError as exc:
        assert "secret-sentinel" not in str(exc)


if __name__ == "__main__":
    main()
