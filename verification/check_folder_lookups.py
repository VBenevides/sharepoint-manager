"""Check that sync folder creation reuses known missing states."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager import SharepointManager
from sharepoint_manager.dataclasses import SPFolder
from sharepoint_manager.exceptions import SPConflictError, SPFolderNotFound


def _manager(existing: set[str], conflict: bool = False):
    manager = object.__new__(SharepointManager)
    manager._drive_id = "drive"
    calls: list[tuple[str, str]] = []
    folders = {"": SPFolder(id="root", name="")}

    def get_folder(path: str) -> SPFolder:
        calls.append(("GET", path))
        if path not in existing and path not in folders:
            raise SPFolderNotFound("missing")
        return folders.setdefault(
            path, SPFolder(id=path or "root", name=path.rsplit("/", 1)[-1])
        )

    def create_folder(parent_id: str, name: str) -> SPFolder:
        path = f"{parent_id}/{name}" if parent_id else name
        calls.append(("POST", path))
        folder = SPFolder(id=path, name=name)
        if conflict:
            existing.add(name)
            folders[name] = folder
            raise SPConflictError("conflict")
        folders[path] = folder
        return folder

    manager._get_folder = get_folder
    manager._create_single_folder = create_folder
    return manager, calls


def main() -> None:
    manager, calls = _manager({"existing"})
    assert manager._resolve_folder("existing").name == "existing"
    assert calls == [("GET", "existing")]

    manager, calls = _manager(set())
    assert manager._resolve_folder("new", create_folder=True).name == "new"
    assert calls == [("GET", "new"), ("GET", ""), ("POST", "root/new")]

    manager, calls = _manager(set())
    assert manager._resolve_folder("one/two", create_folder=True).name == "two"
    assert calls == [
        ("GET", "one/two"),
        ("GET", "one"),
        ("GET", ""),
        ("POST", "root/one"),
        ("POST", "root/one/two"),
    ]

    manager, calls = _manager(set(), conflict=True)
    assert manager._resolve_folder("new", create_folder=True).name == "new"
    assert calls == [("GET", "new"), ("GET", ""), ("POST", "root/new"), ("GET", "new")]


if __name__ == "__main__":
    main()
