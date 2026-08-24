import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import OperationPolicy


class Response:
    status_code = 503
    headers = {}

    def close(self):
        return None


class Session:
    def __init__(self):
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs["method"])
        return Response()


def main() -> None:
    session = Session()
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.tenant_url = "https://tenant.sharepoint.com"
    manager.policy = OperationPolicy(max_retry_attempts=3, max_retry_after_seconds=0.01)
    manager._session = session
    from sharepoint_manager import core

    original_sleep = core.time.sleep
    core.time.sleep = lambda _: None
    try:
        manager._request(
            "POST",
            "https://graph.microsoft.com/v1.0/items",
            headers={"Authorization": "Bearer token"},
        )
        assert session.calls == ["POST"]
        session.calls.clear()
        manager._request(
            "GET",
            "https://graph.microsoft.com/v1.0/items",
            headers={"Authorization": "Bearer token"},
        )
        assert session.calls == ["GET", "GET", "GET"]
    finally:
        core.time.sleep = original_sleep


if __name__ == "__main__":
    main()
