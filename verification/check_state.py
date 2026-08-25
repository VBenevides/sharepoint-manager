import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager.dataclasses import SPFile, SPFolder


def main() -> None:
    old = SPFolder(id="old")
    target = SPFolder(id="target")
    manager = object.__new__(SharepointManager)
    manager._root_folder = old
    manager._resolve_folder = lambda path, create_folder=False: target
    manager._list_children = lambda folder: ({"file": SPFile(id="file")}, {})
    assert list(manager.list_files("Folder")) == ["file"]
    assert manager._root_folder is old
    assert manager.list_folders("Folder") == {}
    assert manager._root_folder is old

    source = (Path(__file__).parents[1] / "sharepoint_manager/core.py").read_text()
    assert "def set_folder(" not in source
    assert "def cwd(" not in source
    assert "self.folder" not in source


if __name__ == "__main__":
    main()
