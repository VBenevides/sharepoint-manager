import io
import sys
import types
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import OperationPolicy, SPFile
from sharepoint_manager.exceptions import SPValidationError


class NonSeekable(io.BytesIO):
    def seekable(self):
        return False

    def seek(self, *args):
        raise OSError("not seekable")


def main() -> None:
    manager = object.__new__(SharepointManager)
    manager.policy = OperationPolicy(
        max_file_bytes=100, max_total_bytes=100, max_disk_bytes=100
    )
    manager._root_folder = types.SimpleNamespace(id="folder")
    manager._check_file_budget = lambda size, destination=None: None
    manager._validate_file_boundary = lambda file: None
    downloaded = SPFile(id="file", name="file.txt", size=3)
    manager._get_file = lambda name, folder: downloaded
    manager._stream_download = lambda file, output: output.write(b"abc")
    output = io.BytesIO()
    assert manager.download_fileobj("file.txt", output).id == "file"
    assert output.getvalue() == b"abc"

    class Response:
        headers: ClassVar[dict[str, str]] = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size):
            yield b"abc"
            yield b"de"

    manager._request = lambda *args, **kwargs: Response()
    manager._raise_for_status = lambda response: None
    output = io.BytesIO()
    try:
        SharepointManager._stream_download(
            manager,
            SPFile(
                id="oversized",
                name="oversized.bin",
                size=3,
                download_url="https://download.sharepoint.com/file",
            ),
            output,
        )
    except SPValidationError:
        pass
    else:
        raise AssertionError("oversized streamed response accepted")
    assert output.getvalue() == b"abc"

    captured = {}

    def direct(source, filename, folder, size, conflict_behavior):
        captured["seekable"] = source.read()
        captured["filename"] = filename
        return SPFile(id="direct", name=filename, size=size)

    manager._upload_source_direct = direct
    assert (
        manager.upload_fileobj(io.BytesIO(b"seekable"), "seekable.bin").id == "direct"
    )
    assert captured["seekable"] == b"seekable"

    def upload(path, *args, **kwargs):
        captured["data"] = Path(path).read_bytes()
        return SPFile(id="uploaded", name="data.bin")

    manager.upload_file = upload
    assert manager.upload_fileobj(NonSeekable(b"data"), "data.bin").id == "uploaded"
    assert captured["data"] == b"data"


if __name__ == "__main__":
    main()
