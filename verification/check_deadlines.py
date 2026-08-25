import sys
import time
import types
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

import requests

from sharepoint_manager import AsyncSharepointManager, OperationPolicy
from sharepoint_manager.core import SharepointManager
from sharepoint_manager.exceptions import (
    SPDeadlineExceeded,
    SPDriveNotFound,
    SPFileNotFound,
    SPFolderNotFound,
    SPGraphError,
)


class Response:
    status_code = 404
    headers: ClassVar[dict[str, str]] = {"request-id": "missing-1"}

    def raise_for_status(self):
        raise requests.HTTPError("private details")

    def close(self):
        return None


class SlowSession:
    def request(self, **kwargs):
        time.sleep(0.02)
        return Response()


def main() -> None:
    manager = object.__new__(SharepointManager)
    for error_type in (SPFileNotFound, SPFolderNotFound):
        try:
            manager._raise_for_status(Response(), not_found=error_type)
        except error_type as exc:
            assert exc.status == 404
            assert exc.request_id == "missing-1"
            assert not exc.retryable
            assert isinstance(exc.cause, requests.HTTPError)
            assert "private details" not in str(exc)
        else:
            raise AssertionError("missing resource was not mapped")

    manager.graph_host = "graph.microsoft.com"
    manager.tenant_url = "https://tenant.sharepoint.com"
    manager.policy = OperationPolicy(wall_clock_seconds=0.01)
    manager._session = SlowSession()
    manager._owns_session = False
    manager._closed = False
    try:
        manager._request(
            "GET",
            "https://graph.microsoft.com/v1.0/items",
            headers={"Authorization": "Bearer token"},
        )
    except SPDeadlineExceeded as exc:
        assert exc.retryable
    else:
        raise AssertionError("slow request exceeded its deadline silently")

    url_manager = object.__new__(SharepointManager)
    url_manager.graph_host = "graph.microsoft.com"
    url_manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    url_manager._hdr = lambda: {"Authorization": "Bearer token"}
    url_manager._request = lambda method, url, **kwargs: Response()
    share_url = "https://tenant.sharepoint.com/sites/demo/Documents/missing"
    for method, error_type in (
        (url_manager.get_file_metadata_from_url, SPFileNotFound),
        (url_manager.get_folder_metadata_from_url, SPFolderNotFound),
    ):
        try:
            method(share_url)
        except error_type as exc:
            assert exc.status == 404
        else:
            raise AssertionError("missing URL resource was not mapped")

    drive_manager = object.__new__(SharepointManager)
    drive_manager.document_folder_name = "Documents"
    drive_manager._site_id = "site"
    drive_manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    drive_manager._paginate = lambda url: iter(())
    try:
        drive_manager._get_drive_id()
    except SPDriveNotFound as exc:
        assert exc.status == 404
    else:
        raise AssertionError("missing drive was not mapped")

    async_manager = object.__new__(AsyncSharepointManager)
    async_manager.policy = types.SimpleNamespace(wall_clock_seconds=1)
    async_manager._correlation_id = "check"
    async_manager.telemetry = None
    try:
        async_manager._raise_for_status(Response(), not_found=SPGraphError)
    except SPGraphError as exc:
        assert exc.status == 404
        assert exc.request_id == "missing-1"
    else:
        raise AssertionError("async missing resource was not mapped")


if __name__ == "__main__":
    main()
