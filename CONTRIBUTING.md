# Contributing

Use Python 3.10 or newer. Install the development extra, then run:

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m compileall -q sharepoint_manager verification tests
ruff format --check tests verification sharepoint_manager
ruff check
python -m build --sdist --wheel
```

The focused checks use fake authentication and do not need tenant credentials.
Do not run destructive Graph operations against production. Staging tests use
only tagged disposable content and require explicit configuration for writes.

Use `type(scope): subject` commit messages. Releases are immutable `vX.Y.Z`
tags; the protected release workflow checks `VERSION`, `CHANGELOG.md`, build
metadata, attestations, and the published installation.
