"""Offline asynchronous workflow using direct SharePoint URLs."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from sharepoint_manager import AsyncSharepointManager


async def run(manager: AsyncSharepointManager, source: Path, destination: Path) -> None:
    """Upload and download one file through the async public URL methods."""
    folder_url = "https://tenant.sharepoint.com/sites/demo/Documents/Reports"
    file_url = f"{folder_url}/2026.txt"
    await manager.upload_file_to_folder_url(folder_url, str(source))
    await manager.download_file_from_url(file_url, str(destination))


async def main() -> None:
    """Run the async URL example with an offline manager mock."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "2026.txt"
        destination = root / "downloads"
        source.write_text("offline\n", encoding="utf-8")
        manager = AsyncMock(spec=AsyncSharepointManager)
        await run(manager, source, destination)
        manager.upload_file_to_folder_url.assert_awaited_once()
        manager.download_file_from_url.assert_awaited_once()
    print("offline asynchronous URL example complete")


if __name__ == "__main__":
    asyncio.run(main())
