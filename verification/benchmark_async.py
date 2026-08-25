"""Measure local async transfer throughput and event-loop lag."""

import asyncio
import json
import sys
import tempfile
import time
import types
from math import ceil
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager import AsyncSharepointManager, OperationPolicy


class Response:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, payload=None, content=b""):
        self.payload = payload or {}
        self.content = content

    def json(self):
        return self.payload


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.items = {}

    async def request(self, method, url, **kwargs):
        await asyncio.sleep(0.001)
        if "/shares/" in url:
            share = url.split("/shares/", 1)[1].split("/", 1)[0]
            return Response(self.items[share])
        if "/download/" in url:
            return Response(content=self.payload)
        if method == "PUT" and url.endswith("/content"):
            data = kwargs.get("content", b"")
            return Response({"id": "uploaded", "size": len(data)})
        return Response()


class Provider:
    def get_token(self, scope):
        return "benchmark-token"


async def heartbeat(stop: asyncio.Event, interval: float, lags: list[float]) -> None:
    deadline = time.perf_counter() + interval
    while not stop.is_set():
        await asyncio.sleep(max(0.0, deadline - time.perf_counter()))
        lags.append(max(0.0, time.perf_counter() - deadline))
        deadline += interval


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, ceil(len(ordered) * fraction) - 1)]


async def main() -> None:
    size = 4 * 1024**2
    workers = 4
    payload = b"x" * size
    client = Client(payload)
    manager = AsyncSharepointManager(
        "https://tenant.sharepoint.com/sites/demo",
        token_provider=Provider(),
        policy=OperationPolicy(max_concurrency=workers),
        client=client,
    )
    manager._site_id = "site"
    manager._drive_id = "drive"
    with tempfile.TemporaryDirectory() as directory:
        folder_url = "https://tenant.sharepoint.com/sites/demo/Documents/folder"
        client.items[manager._share_id(folder_url)] = {
            "id": "folder",
            "name": "folder",
            "folder": {},
            "parentReference": {"siteId": "site", "driveId": "drive"},
        }
        transfers = []
        for index in range(workers):
            url = f"https://tenant.sharepoint.com/sites/demo/Documents/file-{index}.bin"
            client.items[manager._share_id(url)] = {
                "id": f"file-{index}",
                "name": f"file-{index}.bin",
                "size": size,
                "file": {},
                "@microsoft.graph.downloadUrl": f"https://tenant.sharepoint.com/download/{index}",
                "parentReference": {"siteId": "site", "driveId": "drive"},
            }
            source = Path(directory) / f"source-{index}.bin"
            source.write_bytes(payload)
            destination = Path(directory) / f"destination-{index}.bin"
            transfers.extend(
                (
                    manager.upload_file_to_folder_url(folder_url, str(source)),
                    manager.download_file_from_url(url, str(destination)),
                )
            )
        lags: list[float] = []
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat(stop, 0.001, lags))
        await asyncio.sleep(0)
        started = time.perf_counter()
        await asyncio.gather(*transfers)
        elapsed = time.perf_counter() - started
        stop.set()
        await task
    await manager.close()
    result = {
        "storage": "local temporary filesystem",
        "workers": workers,
        "bytes_transferred": 2 * workers * size,
        "throughput_mib_s": round(2 * workers * size / elapsed / 1024**2, 3),
        "heartbeat_samples": len(lags),
        "heartbeat_p95_ms": round(percentile(lags, 0.95) * 1000, 3),
        "heartbeat_p99_ms": round(percentile(lags, 0.99) * 1000, 3),
        "offload": "not applied; local baseline only",
        "network_storage": "not measured",
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
