import re
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    init = (root / "sharepoint_manager/__init__.py").read_text(encoding="utf-8")
    assert re.search(r'requires-python = ">=3\.10"', pyproject)
    assert all(
        dependency in pyproject
        for dependency in ("httpx>=0.27,<1", "msal>=1.37,<2", "requests>=2.33,<3")
    )
    assert 'build-backend = "setuptools.build_meta"' in pyproject
    assert not (root / "setup.py").exists()
    assert '"build==1.3.0"' in pyproject
    assert '"ruff==0.12.10"' in pyproject
    assert not (root / "requirements.txt").exists()
    assert not (root / "requirements-build.txt").exists()
    assert "VERSION" in init
    assert version == "0.1.0"

    code = """
import importlib.metadata
import sys
import types

importlib.metadata.version = lambda name: (_ for _ in ()).throw(
    importlib.metadata.PackageNotFoundError(name)
)
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules["msal"] = msal
sys.path.insert(0, ".")
import sharepoint_manager
assert sharepoint_manager.__version__ == "0.1.0"
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    main()
