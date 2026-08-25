from pathlib import Path


def main() -> None:
    workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text()
    for required in (
        '"3.10"',
        '"3.14"',
        "minimum",
        "latest",
        "name: Static checks",
        "needs: [checks, static]",
        "python -m unittest discover -s tests -v",
        "ruff format --check",
        "ruff check",
        "pyright sharepoint_manager",
        "python -m build",
        "pip-audit --progress-spinner off",
    ):
        assert required in workflow, required


if __name__ == "__main__":
    main()
