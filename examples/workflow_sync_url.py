"""Offline synchronous workflow using direct SharePoint URLs."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from sharepoint_manager import SharepointManager


def run(manager: SharepointManager, source: Path, destination: Path) -> None:
    """Upload and download one file with explicit URL and path arguments."""
    folder_url = "https://tenant.sharepoint.com/sites/demo/Documents/Reports"
    file_url = f"{folder_url}/2026.txt"
    manager.upload_file_to_folder_url(folder_url, str(source))
    manager.download_file_from_url(file_url, str(destination))


def main() -> None:
    """Run the URL workflow with an offline manager mock."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "2026.txt"
        destination = root / "downloads"
        source.write_text("offline\n", encoding="utf-8")
        manager = Mock(spec=SharepointManager)
        run(manager, source, destination)
        manager.upload_file_to_folder_url.assert_called_once()
        manager.download_file_from_url.assert_called_once()
    print("offline synchronous URL example complete")


if __name__ == "__main__":
    main()
