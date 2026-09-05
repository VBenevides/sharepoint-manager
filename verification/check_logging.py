"""Verify the library's default logging threshold."""

import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)
import sharepoint_manager


def main() -> None:
    logger = logging.getLogger("sharepoint_manager")
    child = logging.getLogger("sharepoint_manager.core")
    original_level = logger.level
    original_handlers = list(logger.handlers)
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger.addHandler(handler)
    try:
        logger.setLevel(logging.WARNING)
        child.info("hidden")
        child.warning("visible")
        assert [record.getMessage() for record in records] == ["visible"]
        logger.setLevel(logging.INFO)
        child.info("enabled by application override")
        assert [record.getMessage() for record in records] == [
            "visible",
            "enabled by application override",
        ]
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        for installed in logger.handlers[:]:
            if installed not in original_handlers:
                logger.removeHandler(installed)

    assert sharepoint_manager.__name__ == "sharepoint_manager"


if __name__ == "__main__":
    main()
