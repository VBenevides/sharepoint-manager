import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

import requests

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import SPFile
from sharepoint_manager.exceptions import SPAmbiguousWriteError, SPGraphError
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
    error = SPAmbiguousWriteError(canary, RuntimeError(canary))
    assert canary not in repr(error) and canary not in str(error)
    assert not hasattr(error, "upload_url")
    assert error.__cause__ is None

    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.tenant_url = "https://tenant.sharepoint.com"
    manager.policy = types.SimpleNamespace(
        wall_clock_seconds=1,
        max_retry_attempts=1,
        allow_capability_redirects=False,
        max_retry_after_seconds=1,
    )
    manager._closed = False
    manager._emit_telemetry = lambda *args, **kwargs: None
    manager._perform_request = lambda **kwargs: (_ for _ in ()).throw(
        requests.RequestException(canary)
    )
    try:
        manager._request("GET", canary, authenticated=False)
    except SPGraphError as exc:
        assert canary not in str(exc)
        assert exc.__cause__ is None
    else:
        raise AssertionError("capability transport failure was not translated")

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
