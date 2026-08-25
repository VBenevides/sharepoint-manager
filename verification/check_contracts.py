"""Check observable parity between the synchronous and async clients."""

import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

import requests

from sharepoint_manager import (
    AsyncSharepointManager,
    OperationPolicy,
    SharepointManager,
)
from sharepoint_manager.dataclasses import SPFile
from sharepoint_manager.exceptions import (
    SPAuthorizationError,
    SPConflictError,
    SPGraphError,
    SPNotFoundError,
    SPThrottledError,
    SPUnauthorizedTarget,
    SPValidationError,
)


class Response:
    def __init__(self, status: int):
        self.status_code = status
        self.headers = {"request-id": "request"}

    def json(self):
        return {"error": {"message": "safe detail"}}

    def raise_for_status(self):
        raise requests.RequestException("transport detail")


def _raise(manager, response: Response):
    try:
        manager._raise_for_status(response)
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("status was accepted")


def _status_parity() -> None:
    expected = {
        401: SPAuthorizationError,
        403: SPAuthorizationError,
        404: SPNotFoundError,
        409: SPConflictError,
        429: SPThrottledError,
        500: SPGraphError,
    }
    sync = object.__new__(SharepointManager)
    sync._emit_telemetry = lambda *args, **kwargs: None
    async_manager = object.__new__(AsyncSharepointManager)
    async_manager._emit = lambda *args, **kwargs: None
    for status, error_type in expected.items():
        sync_error = _raise(sync, Response(status))
        async_error = _raise(async_manager, Response(status))
        assert type(sync_error) is error_type
        assert type(async_error) is error_type
        assert sync_error.status == async_error.status == status


def _boundary_parity() -> None:
    sync = object.__new__(SharepointManager)
    sync._site_id = "site"
    sync._drive_id = "drive"
    async_manager = object.__new__(AsyncSharepointManager)
    async_manager._site_id = "site"
    async_manager._drive_id = "drive"
    matching = {"driveId": "drive", "siteId": "site"}
    file = SPFile(id="file", parent_reference=matching)
    sync._validate_file_boundary(file)
    async_manager._validate_boundary({"parentReference": matching})
    for parent in ({"driveId": "other"}, {"driveId": "drive", "siteId": "other"}):
        try:
            sync._validate_file_boundary(SPFile(id="file", parent_reference=parent))
        except SPUnauthorizedTarget:
            pass
        else:
            raise AssertionError("sync boundary accepted an unrelated file")
        try:
            async_manager._validate_boundary({"parentReference": parent})
        except SPUnauthorizedTarget:
            pass
        else:
            raise AssertionError("async boundary accepted an unrelated item")


def _budget_parity() -> None:
    policy = OperationPolicy(max_file_bytes=10, max_total_bytes=10, max_disk_bytes=10)
    sync = object.__new__(SharepointManager)
    sync.policy = policy
    async_manager = object.__new__(AsyncSharepointManager)
    async_manager.policy = policy
    for consume in (
        lambda budget: sync._consume_operation_budget(budget, byte_count=11),
        lambda budget: async_manager._consume_budget(budget, byte_count=11),
    ):
        try:
            consume({"bytes": 0, "items": 0, "pages": 0, "started": time.monotonic()})
        except SPValidationError:
            pass
        else:
            raise AssertionError("budget overflow was accepted")


def _telemetry_parity() -> None:
    sync_events = []
    async_events = []
    sync = object.__new__(SharepointManager)
    sync.telemetry = sync_events.append
    async_manager = object.__new__(AsyncSharepointManager)
    async_manager._correlation_id = "correlation"
    async_manager.telemetry = async_events.append
    sync._emit_telemetry("graph.error", status=401, outcome="failure")
    async_manager._emit("graph.error", status=401, outcome="failure")
    for event in (sync_events[0], async_events[0]):
        event.pop("correlation_id", None)
    assert sync_events == async_events


def main() -> None:
    _status_parity()
    _boundary_parity()
    _budget_parity()
    _telemetry_parity()


if __name__ == "__main__":
    main()
