from pathlib import Path


def main() -> None:
    root = Path(__file__).parents[1]
    targets = (root / "docs/targets.md").read_text()
    benchmark = (root / "verification/benchmark_targets.py").read_text()
    for required in (
        "10 GiB",
        "100 GiB",
        "p95 ≤ 1.5 seconds",
        "Retry-After",
        "Atomic downloads",
        "python verification/benchmark_targets.py",
    ):
        assert required in targets, required
    for required in ("QuickXorHash", "SPFile.from_dict", "timeit.repeat"):
        assert required in benchmark, required


if __name__ == "__main__":
    main()
