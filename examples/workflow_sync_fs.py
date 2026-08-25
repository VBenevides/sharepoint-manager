"""Offline synchronous folder workflow using explicit filesystem paths."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from sharepoint_manager import SharepointManager


def run(manager: SharepointManager, source: Path, destination: Path) -> None:
    """Move a local tree to and from one explicit SharePoint folder."""
    remote_folder = "https://tenant.sharepoint.com/sites/demo/Documents/Reports"
    manager.upload_folder_to_folder_url(remote_folder, str(source))
    manager.download_folder_from_url(remote_folder, str(destination))


def main() -> None:
    """Run the filesystem workflow with an offline manager mock."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source" / "Reports"
        destination = root / "destination"
        source.mkdir(parents=True)
        (source / "2026.txt").write_text("offline\n", encoding="utf-8")
        manager = Mock(spec=SharepointManager)
        run(manager, source, destination)
        manager.upload_folder_to_folder_url.assert_called_once()
        manager.download_folder_from_url.assert_called_once()
    print("offline synchronous filesystem example complete")


if __name__ == "__main__":
    main()
