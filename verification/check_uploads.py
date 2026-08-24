import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager  # noqa: E402
from sharepoint_manager.dataclasses import OperationPolicy, SPFolder  # noqa: E402
from sharepoint_manager.exceptions import SPAmbiguousWriteError  # noqa: E402


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body
        self.headers = {}

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")


def manager_for(responses):
    manager = object.__new__(SharepointManager)
    manager._site_id = "site"
    manager._drive_id = "drive"
    manager._graph_base_url = "https://graph.microsoft.com/v1.0"
    manager.policy = OperationPolicy(
        max_file_bytes=100, max_total_bytes=100, max_disk_bytes=100
    )
    manager.folder = SPFolder(id="folder", name="root")
    manager._hdr = lambda json_content=False: {"Authorization": "Bearer token"}
    manager._request = lambda method, url, **kwargs: responses.pop(0)
    return manager


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        empty = Path(directory) / "empty.txt"
        empty.write_bytes(b"")
        manager = manager_for(
            [
                Response(200, {"uploadUrl": "https://upload.sharepoint.com/session"}),
                Response(201, {"id": "empty", "name": "empty.txt", "file": {}}),
            ]
        )
        result = manager.upload_file(str(empty), _folder=manager.folder)
        assert result.id == "empty"

        data = Path(directory) / "data.txt"
        data.write_bytes(b"abc")
        calls = []
        responses = [
            Response(200, {"uploadUrl": "https://upload.sharepoint.com/session"}),
            Response(202, {"nextExpectedRanges": ["0-3"]}),
            Response(201, {"id": "data", "name": "data.txt", "file": {}}),
        ]
        manager = manager_for(responses)
        original_request = manager._request
        manager._request = lambda method, url, **kwargs: (
            calls.append((method, kwargs.get("data")))
            or original_request(method, url, **kwargs)
        )
        assert manager.upload_file(str(data), _folder=manager.folder).id == "data"
        assert [call[0] for call in calls] == ["POST", "PUT", "PUT"]

        manager = manager_for(
            [Response(200, {"uploadUrl": "https://upload.sharepoint.com/session"})]
        )
        manager._request = lambda method, url, **kwargs: (
            (_ for _ in ()).throw(RuntimeError("network"))
            if method == "PUT"
            else Response(200, {"uploadUrl": "https://upload.sharepoint.com/session"})
        )
        try:
            manager.upload_file(str(data), _folder=manager.folder)
        except SPAmbiguousWriteError:
            pass
        else:
            raise AssertionError("ambiguous upload was hidden")


if __name__ == "__main__":
    main()
