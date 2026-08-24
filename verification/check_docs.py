from pathlib import Path


def main() -> None:
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text()
    contributing = (root / "CONTRIBUTING.md").read_text()
    security = (root / "SECURITY.md").read_text()
    for required in (
        "Sites.Selected",
        "TokenProvider",
        "OperationPolicy",
        "iter_collection()",
        "iter_folder_delta()",
        "SPAmbiguousWriteError",
        "QuickXorHash",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ):
        assert required in readme, required
    assert "verification/run_core_checks.py" in contributing
    assert "least-privilege staging site" in security


if __name__ == "__main__":
    main()
