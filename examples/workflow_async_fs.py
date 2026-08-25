"""Offline asynchronous filesystem workflow.

The current async filesystem workflow delegates the synchronous workflow to a
worker thread, keeping local reads and writes off the event-loop thread.
"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from workflow_sync_fs import run as run_sync

from sharepoint_manager import SharepointManager


async def run(manager: SharepointManager, source: Path, destination: Path) -> None:
    """Run the synchronous filesystem workflow through ``asyncio.to_thread``."""
    await asyncio.to_thread(run_sync, manager, source, destination)


async def main() -> None:
    """Run the async filesystem example without network access."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source" / "Reports"
        destination = root / "destination"
        source.mkdir(parents=True)
        (source / "2026.txt").write_text("offline\n", encoding="utf-8")
        manager = Mock(spec=SharepointManager)
        await run(manager, source, destination)
        manager.upload_folder_to_folder_url.assert_called_once()
        manager.download_folder_from_url.assert_called_once()
    print("offline asynchronous filesystem example complete")


if __name__ == "__main__":
    asyncio.run(main())
