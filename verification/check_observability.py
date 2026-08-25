import io
import logging
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
from sharepoint_manager.dataclasses import OperationPolicy, SPFile
from sharepoint_manager.exceptions import SPAuthorizationError


class Response:
    status_code = 200
    headers = {"request-id": "request-a"}

    def __init__(self, body=None, chunks=()):
        self.body = body or {}
        self.chunks = chunks
        self.closed = False

    def json(self):
        return self.body

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Session:
    def request(self, **kwargs):
        return Response()


def main() -> None:
    events = []
    log_records = []

    class Handler(logging.Handler):
        def emit(self, record):
            event = getattr(record, "sharepoint_event", None)
            if event is not None:
                log_records.append(event)

    handler = Handler()
    core_logger = logging.getLogger("sharepoint_manager.core")
    core_logger.addHandler(handler)
    core_logger.setLevel(logging.INFO)
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.tenant_url = "https://tenant.sharepoint.com"
    manager.policy = OperationPolicy(max_pages=2, max_items=4)
    manager._closed = False
    manager._session = Session()
    manager._request_lock = threading.Lock()
    manager.telemetry = events.append
    manager._hdr = lambda: {"Authorization": "Bearer secret-token"}

    response = manager._request(
        "GET",
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": "Bearer secret-token"},
        authenticated=True,
    )
    response.close()
    request_event = next(event for event in events if event["event"] == "graph.request")
    assert request_event["status"] == 200
    assert request_event["request_id"] == "request-a"
    assert request_event["operation"] == "request"
    assert request_event["elapsed_ms"] >= 0
    assert request_event["correlation_id"]
    assert "url" not in request_event and "secret-token" not in repr(request_event)
    logged_request = next(
        event for event in log_records if event["event"] == "graph.request"
    )
    assert logged_request["correlation_id"] == request_event["correlation_id"]

    failed = Response()
    failed.status_code = 403
    failed.headers = {"request-id": "request-b"}
    failed.raise_for_status = lambda: None
    try:
        manager._raise_for_status(failed)
    except SPAuthorizationError:
        pass
    else:
        raise AssertionError("HTTP failure was hidden")
    error_event = next(event for event in events if event["event"] == "graph.error")
    assert error_event["failure_class"] == "SPAuthorizationError"
    assert error_event["correlation_id"] == request_event["correlation_id"]
    assert any(event["event"] == "graph.error" for event in log_records)
    core_logger.removeHandler(handler)

    manager._drive_id = "drive-a"
    manager._request = lambda method, url, **kwargs: Response(
        {"value": [{"id": "item-a"}]}
    )
    assert (
        list(manager._paginate("https://graph.microsoft.com/v1.0/items"))[0]["id"]
        == "item-a"
    )
    page_event = next(event for event in events if event["event"] == "graph.page")
    assert page_event["items"] == 1 and page_event["total_items"] == 1

    manager._request = lambda method, url, **kwargs: Response(chunks=[b"abc"])
    output = io.BytesIO()
    manager.download_fileobj(
        SPFile(
            id="file-a",
            size=3,
            download_url="https://cdn.example/file",
            parent_reference={"driveId": "drive-a"},
        ),
        output,
    )
    transfer = next(event for event in events if event["event"] == "transfer")
    assert transfer["operation"] == "download"
    assert transfer["bytes"] == 3 and transfer["outcome"] == "success"
    assert output.getvalue() == b"abc"


if __name__ == "__main__":
    main()
