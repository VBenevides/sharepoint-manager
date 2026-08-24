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
from sharepoint_manager.dataclasses import SPFile  # noqa: E402


class Response:
    def __init__(self, chunks, fail=False):
        self.chunks = chunks
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise RuntimeError("stream interrupted")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "file.txt"
        target.write_bytes(b"old")
        file_obj = SPFile(
            id="file",
            name="file.txt",
            size=3,
            download_url="https://download.sharepoint.com/file",
        )
        manager = object.__new__(SharepointManager)
        manager._request = lambda *args, **kwargs: Response([b"new"])
        manager._download_to_path(file_obj, str(target))
        assert target.read_bytes() == b"new"

        target.write_bytes(b"old")
        manager._request = lambda *args, **kwargs: Response([b"x"], fail=True)
        try:
            manager._download_to_path(file_obj, str(target))
        except RuntimeError:
            pass
        else:
            raise AssertionError("interrupted stream succeeded")
        assert target.read_bytes() == b"old"
        assert list(Path(directory).glob(".sp-download-*")) == []


if __name__ == "__main__":
    main()
