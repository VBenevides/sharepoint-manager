import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager


class Resource:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def main() -> None:
    session = Resource()
    provider = Resource()
    manager = object.__new__(SharepointManager)
    manager._session = session
    manager._owns_session = True
    manager._token_provider = provider
    manager._closed = False
    manager._close_lock = __import__("threading").Lock()
    with manager as entered:
        assert entered is manager
    manager.close()
    assert session.closed == 1
    assert provider.closed == 1

    manager = object.__new__(SharepointManager)
    manager._session = session
    manager._owns_session = False
    manager._token_provider = None
    manager._closed = False
    manager._close_lock = __import__("threading").Lock()
    manager.close()
    assert session.closed == 1


if __name__ == "__main__":
    main()
