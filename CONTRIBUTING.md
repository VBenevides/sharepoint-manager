# Contributing

Use Python 3.10 or newer. Install runtime and build dependencies, then run:

```bash
python verification/run_core_checks.py
python -m compileall -q sharepoint_manager verification
ruff format --check verification sharepoint_manager/__init__.py sharepoint_manager/dataclasses.py sharepoint_manager/exceptions.py sharepoint_manager/utils.py
ruff check --select E4,E7,E9,F --ignore E402 sharepoint_manager verification
python -m build --sdist --wheel
```

The focused checks use fake authentication and do not need tenant credentials.
Do not run destructive Graph operations against production. Staging tests use
only tagged disposable content and require explicit configuration for writes.

Use `type(scope): subject` commit messages. Releases are immutable `vX.Y.Z`
tags; the protected release workflow checks `VERSION`, `CHANGELOG.md`, build
metadata, attestations, and the published installation.
