import io
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import OperationPolicy, SPFile


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
