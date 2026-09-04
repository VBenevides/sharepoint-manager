import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import SPFile
from sharepoint_manager.exceptions import SPFileIntegrityError
from sharepoint_manager.utils import QuickXorHash


class Response:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, **_kwargs):
        yield self.content[:2]
        yield self.content[2:]


def main() -> None:
    content = b"hash me"
    digest = QuickXorHash()
    digest.update(content)
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "file.txt"
        target.write_bytes(b"old")
        file_obj = SPFile(
            id="file",
            name="file.txt",
            size=len(content),
            file={"hashes": {"quickXorHash": digest.b64digest()}},
            download_url="https://download.sharepoint.com/file",
        )
        manager = object.__new__(SharepointManager)
        manager._request = lambda *args, **kwargs: Response(content)
        manager._download_to_path(file_obj, str(target))
        assert target.read_bytes() == content

        target.write_bytes(b"old")
        file_obj.file = {"hashes": {"quickXorHash": "wrong"}}
        try:
            manager._download_to_path(file_obj, str(target))
        except SPFileIntegrityError:
            pass
        else:
            raise AssertionError("integrity mismatch accepted")
        assert target.read_bytes() == b"old"
        assert list(Path(directory).glob(".sp-download-*")) == []


if __name__ == "__main__":
    main()
