import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

import requests  # noqa: E402
from sharepoint_manager.core import SharepointManager  # noqa: E402
from sharepoint_manager.exceptions import (  # noqa: E402
    SPGraphError,
    SPThrottledError,
    SPValidationError,
)


class Response:
    status_code = 429
    headers = {"request-id": "req-1"}

    def raise_for_status(self):
        raise requests.HTTPError("private response details")


def main() -> None:
    manager = object.__new__(SharepointManager)
    try:
        manager._raise_for_status(Response())
    except SPThrottledError as exc:
        assert exc.status == 429
        assert exc.request_id == "req-1"
        assert exc.retryable
        assert isinstance(exc.cause, requests.HTTPError)
        assert "private response details" not in str(exc)
    else:
        raise AssertionError("HTTP failure was not translated")
    assert issubclass(SPValidationError, ValueError)
    assert issubclass(SPGraphError, Exception)


if __name__ == "__main__":
    main()
