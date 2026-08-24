from pathlib import Path
import re


def main() -> None:
    root = Path(__file__).parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    setup = (root / "setup.py").read_text(encoding="utf-8")
    init = (root / "sharepoint_manager/__init__.py").read_text(encoding="utf-8")
    assert re.search(r'requires-python = ">=3\.10"', pyproject)
    assert 'msal>=1.28,<2' in pyproject and 'requests>=2.31,<3' in pyproject
    assert 'read_text(encoding="utf-8")' in setup
    assert "VERSION" in init
    assert version == "0.1.0"


if __name__ == "__main__":
    main()
