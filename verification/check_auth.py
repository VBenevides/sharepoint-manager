import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager


class Provider:
    def __init__(self):
        self.calls = 0
        self.closed = False

    def get_token(self, scope):
        assert scope == "https://graph.microsoft.com/.default"
        self.calls += 1
        return types.SimpleNamespace(token="injected-token", expires_on=2**31)

    def close(self):
        self.closed = True


def main() -> None:
    provider = Provider()
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager._token_lock = threading.Lock()
    manager._token_provider = provider
    manager._session = types.SimpleNamespace(close=lambda: None)
    assert manager._ensure_token() == "injected-token"
    assert manager._ensure_token() == "injected-token"
    assert provider.calls == 1
    manager.close()
    assert provider.closed


if __name__ == "__main__":
    main()
