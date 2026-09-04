import os
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
from sharepoint_manager.dataclasses import OperationPolicy, SPFile, SPFolder
from sharepoint_manager.exceptions import SPValidationError
from sharepoint_manager.utils import validate_archive_members

_FIRST_FILE_NAME = "one.txt"
_SECOND_FILE_NAME = "two.txt"


def main() -> None:
    policy = OperationPolicy(max_file_bytes=10, max_total_bytes=20, max_disk_bytes=20)
    assert policy.max_pages == 1000
    for kwargs in (
        {"max_pages": 0},
        {"wall_clock_seconds": float("inf")},
        {"max_file_bytes": 21, "max_total_bytes": 20},
    ):
        try:
            OperationPolicy(**kwargs)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(kwargs)
    validate_archive_members([("folder/file", 4)], 2, 4)
    try:
        validate_archive_members([("../secret", 1)], 2, 4)
    except ValueError:
        pass
    else:
        raise AssertionError("archive traversal accepted")

    policy = OperationPolicy(
        max_file_bytes=10, max_total_bytes=10, max_disk_bytes=10, max_items=10
    )
    manager = object.__new__(SharepointManager)
    manager.policy = policy
    manager._root_folder = SPFolder(id="root", name="")
    manager._resolve_folder = lambda path, create_folder=False: SPFolder(
        id="target", name="tree"
    )
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "tree"
        source.mkdir()
        for name in (_FIRST_FILE_NAME, _SECOND_FILE_NAME):
            Path(source, name).write_bytes(b"123456")

        def upload(path, **kwargs):
            manager._check_file_budget(os.path.getsize(path), _budget=kwargs["_budget"])

        manager.upload_file = upload
        try:
            manager.upload_folder(str(source), _folder=manager._root_folder)
        except SPValidationError as exc:
            assert "byte" in str(exc).lower()
        else:
            raise AssertionError("recursive upload exceeded its aggregate budget")

        manager._list_children = lambda folder, _budget=None: (
            {
                _FIRST_FILE_NAME: SPFile(id="one", name=_FIRST_FILE_NAME, size=6),
                _SECOND_FILE_NAME: SPFile(id="two", name=_SECOND_FILE_NAME, size=6),
            },
            {},
        )

        def download(file, destination, **kwargs):
            manager._check_file_budget(
                file.size, destination, _budget=kwargs["_budget"]
            )

        manager.download_file = download
        try:
            manager.download_folder(
                str(Path(directory) / "out"), _folder=manager._root_folder
            )
        except SPValidationError as exc:
            assert "byte" in str(exc).lower()
        else:
            raise AssertionError("recursive download exceeded its aggregate budget")


if __name__ == "__main__":
    main()
