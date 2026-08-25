# SharePoint Manager

Python clients for SharePoint through Microsoft Graph. Version 0.1.x is an
intentional breaking release from 0.0.x; see the [0.1.x contract](docs/contract-0.1.md).

## Support contract

- Python: 3.10–3.14
- Runtime dependencies: `httpx>=0.27,<1`, `msal>=1.28,<2`, and `requests>=2.31,<3`
- API: explicit operation targets, bounded by `OperationPolicy`, and typed with `SP*` exceptions
- Version: read from `VERSION`; 0.1.x does not preserve the 0.0.x API

The client does not grant end-user permissions. Configure Entra application
permissions outside this package. Prefer `Sites.Selected` and grant only the
required site and drive access; see [deployment permissions](docs/permissions.md).

## Install

```bash
python -m pip install sharepoint-manager
```

For development and release tooling:

```bash
python -m pip install -e ".[dev]"
```

Builds use PEP 517:

```bash
python -m build --sdist --wheel
```

## Quickstart

```python
from sharepoint_manager import ClientCredential, SharepointManager

manager = SharepointManager(
    "https://tenant.sharepoint.com/sites/example",
    ClientCredential("client-id", "client-secret"),
    document_folder_name="Documents",
)

try:
    files = manager.list_files("Reports/2026")
    manager.download_file("summary.csv", "./downloads", "Reports/2026")
finally:
    manager.close()
```

Use `with SharepointManager(...) as manager:` when the manager owns its HTTP
session. Never commit client secrets. For managed identity, workload
federation, certificates, or another provider, inject a `TokenProvider` with
`get_token(scope)`. The explicit service-account workflow is documented in the
[0.1.x contract](docs/contract-0.1.md) and warns when used.

## Operations and guarantees

- Pass an explicit folder path, URL, or object to each operation. Managers have
  no mutable current-folder facade.
- Downloads use a sibling temporary file, verify QuickXorHash when Graph
  supplies one, and replace the destination only after successful completion.
- `upload_file` supports empty files and explicit `fail`, `replace`, or `rename`
  conflict behavior. An uncertain upload raises `SPAmbiguousWriteError` with
  recoverable session state.
- `iter_collection()` yields bounded `SPCollectionPage` values lazily.
  `iter_folder_delta()` yields `SPDeltaPage` values containing files, folders,
  tombstones, and checkpoint links. Persist `delta_link` in the caller's store.
  `get_folder_delta()` remains the materialized compatibility wrapper.
- Approved SharePoint URLs support folder metadata, listing, creation, empty
  deletion, normalized permissions, and file transfers. Site/drive boundaries
  are checked before operations.

## Limits, retries, and errors

Pass `OperationPolicy` to set finite file, byte, page, item, depth, disk,
concurrency, retry, redirect, and wall-clock limits. Safe HTTP methods and
resumable upload chunks use the bounded transport retry policy; non-idempotent
Graph writes are not blindly replayed.

Catch stable public errors such as `SPAuthenticationError`,
`SPAuthorizationError`, `SPThrottledError`, `SPConflictError`,
`SPFolderNotFound`, `SPFileNotFound`, `SPDriveNotFound`, `SPValidationError`,
`SPDeadlineExceeded`, and
`SPAmbiguousWriteError`. Errors retain status, request ID, retryability, and
the original cause without embedding credentials, capability URLs, or names.

## Lifecycle and observability

Managers reuse one session and token provider and close owned resources through
`close()` or context-manager exit. Request concurrency is bounded by the
manager policy and each operation has an explicit target.
Normal logs contain no bearer tokens, credentials, capability URLs, filenames,
or permission members. Graph request IDs are retained as diagnostic fields.
Pass `telemetry=callback` to receive privacy-safe `graph.request`, `graph.page`,
`transfer`, and `auth.token_refresh` records with latency, status, retry,
bytes, page/item, throttle, failure-class, and partial-outcome fields.

## Development, migration, and support

See [CONTRIBUTING.md](CONTRIBUTING.md) for local checks and releases,
[SECURITY.md](SECURITY.md) for security reporting, and
[CHANGELOG.md](CHANGELOG.md) for version history. Use the explicit service-account
flow described in the [0.1.x contract](docs/contract-0.1.md), or inject a
delegated provider/service identity.
