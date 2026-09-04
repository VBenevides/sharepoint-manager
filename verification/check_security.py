import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _MsalClient:
    pass


msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = _MsalClient
msal.PublicClientApplication = _MsalClient
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManagerBase
from sharepoint_manager.exceptions import SPValidationError


def main() -> None:
    manager = object.__new__(SharepointManagerBase)
    manager.graph_host = "graph.microsoft.com"
    manager.tenant_url = "https://tenant.sharepoint.com"

    manager._validate_graph_url("https://graph.microsoft.com/v1.0/me")
    manager._validate_capability_url("https://download.sharepoint.com/file")

    for unsafe in (
        "http://graph.microsoft.com/v1.0/me",  # NOSONAR — rejection test input
        "https://evil.example/v1.0/me",
        "https://graph.microsoft.com:444/v1.0/me",
        "https://user:pass@graph.microsoft.com/v1.0/me",
        "https://graph.microsoft.com/v1.0/me#redirect",
    ):
        try:
            manager._validate_graph_url(unsafe)
        except SPValidationError:
            pass
        else:
            raise AssertionError(unsafe)

    for unsafe in (
        "http://download.sharepoint.com/file",  # NOSONAR — rejection test input
        "https://evil.example/file",
    ):
        try:
            manager._validate_capability_url(unsafe)
        except SPValidationError:
            pass
        else:
            raise AssertionError(unsafe)


if __name__ == "__main__":
    main()
