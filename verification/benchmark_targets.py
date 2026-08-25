import base64
import json
import statistics
import sys
import timeit
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.dataclasses import SPFile
from sharepoint_manager.utils import QuickXorHash


def median_ms(
    statement: str, setup: str, values: dict[str, object], number: int = 1
) -> float:
    samples = timeit.repeat(
        statement, setup=setup, repeat=5, number=number, globals=values
    )
    return statistics.median(samples) * 1000 / number


def main() -> None:
    payload = b"x" * (1024 * 1024)
    digest_ms = median_ms(
        "h = QuickXorHash(); h.update(payload); h.b64digest()",
        "",
        {"QuickXorHash": QuickXorHash, "payload": payload},
    )
    item = {"id": "item-a", "name": "report.csv", "file": {"hashes": {}}}
    metadata_ms = median_ms(
        "SPFile.from_dict(item)",
        "",
        {"SPFile": SPFile, "item": item},
        number=1000,
    )
    result = {
        "python": sys.version.split()[0],
        "payload_bytes": len(payload),
        "quickxor_1m_median_ms": round(digest_ms, 3),
        "metadata_1000_median_ms": round(metadata_ms, 3),
        "quickxor_empty": base64.b64encode(b"").decode(),
    }
    print(json.dumps(result, sort_keys=True))
    if digest_ms > 250 or metadata_ms > 500:
        raise SystemExit("CPU baseline exceeded the documented local target")


if __name__ == "__main__":
    main()
