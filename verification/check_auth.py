import sys
import threading
import types
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import UserDelegatedCredential
from sharepoint_manager.exceptions import SPAuthenticationError

_TEST_USERNAME = "service@example.com"


class PublicClient:
    def __init__(self):
        self.account = {"username": _TEST_USERNAME}
        self.password_calls = 0
        self.silent_calls = 0
        self.use_silent = True

    def get_accounts(self, **_kwargs):
        return [self.account] if self.password_calls else []

    def acquire_token_silent(self, **_kwargs):
        self.silent_calls += 1
        return (
            {"access_token": "silent-token", "expires_in": 3600}
            if self.use_silent
            else None
        )

    def acquire_token_by_username_password(self, username, password, scopes):
        assert username == _TEST_USERNAME
        assert password == "password"
        self.password_calls += 1
        return {"access_token": "bootstrap-token", "expires_in": 3600}


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

    client = PublicClient()
    credential = UserDelegatedCredential("client-id", _TEST_USERNAME, "password")
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.ca = client
    manager.credentials = credential
    manager._token_provider = None
    manager._token_lock = threading.Lock()
    manager._cached_token = ""
    manager._cached_token_expiry = 0
    manager._username = credential.username
    manager._password = credential.password
    manager._account = None
    manager._warned_password_auth = False
    manager.telemetry = lambda event: None

    from sharepoint_manager import core

    original_public_client = core.PublicClientApplication
    core.PublicClientApplication = PublicClient
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            assert manager._ensure_token() == "bootstrap-token"
        assert len(captured) == 1 and captured[0].category is UserWarning
        assert client.password_calls == 1
        assert manager._password is None and manager.credentials is None

        manager._cached_token_expiry = 0
        assert manager._ensure_token() == "silent-token"
        assert client.silent_calls == 1 and client.password_calls == 1

        client.use_silent = False
        manager._cached_token_expiry = 0
        try:
            manager._ensure_token()
        except SPAuthenticationError as exc:
            assert "password" not in str(exc).lower()
        else:
            raise AssertionError("silent cache miss accepted without credentials")
    finally:
        core.PublicClientApplication = original_public_client


if __name__ == "__main__":
    main()
