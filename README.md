# SharePoint Manager

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/VBenevides/sharepoint-manager"></a>
  <a href="https://github.com/VBenevides/sharepoint-manager/blob/main/VERSION"><img alt="Version" src="https://img.shields.io/badge/dynamic/regex.svg?url=https%3A%2F%2Fraw.githubusercontent.com%2FVBenevides%2Fsharepoint-manager%2Frefs%2Fheads%2Fmain%2FVERSION&search=%5E%28.%2B%29%24&label=version&color=blue"></a>
  <a href="https://github.com/VBenevides/sharepoint-manager/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/VBenevides/sharepoint-manager?sort=semver"></a>
  <a href="https://github.com/VBenevides/sharepoint-manager/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/VBenevides/sharepoint-manager/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/VBenevides/sharepoint-manager/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/VBenevides/sharepoint-manager/actions/workflows/codeql.yml/badge.svg"></a>
  <a href="https://github.com/VBenevides/sharepoint-manager/actions/workflows/security.yml"><img alt="Security" src="https://github.com/VBenevides/sharepoint-manager/actions/workflows/security.yml/badge.svg"></a>
</p>

Python clients for SharePoint through Microsoft Graph. Version 0.1.x is an
intentional breaking release from 0.0.x; see the [0.1.x contract](docs/contract-0.1.md).

## Support contract

- Python: 3.10–3.14
- Runtime dependencies: `httpx>=0.27,<1`, `msal>=1.37,<2`, and `requests>=2.33,<3`
- API: explicit operation targets, bounded by `OperationPolicy`, and typed with `SP*` exceptions
- Authentication: confidential app registration credentials or an explicitly
  configured service-account username/password flow
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
session. Never commit client secrets.

For a service account, configure the Entra app registration to allow public
client flows and pass `UserDelegatedCredential`:

```python
import os

from sharepoint_manager import SharepointManager, UserDelegatedCredential

manager = SharepointManager(
    "https://tenant.sharepoint.com/sites/example",
    UserDelegatedCredential(
        os.environ["SP_CLIENT_ID"],
        os.environ["SP_USERNAME"],
        os.environ["SP_PASSWORD"],
    ),
)
```

This username/password flow emits a warning because ROPC does not support MFA
or Conditional Access. Do not persist the service-account password. It is used
only for the initial MSAL token request; subsequent requests use the in-memory
MSAL cache and silent acquisition. If the cache misses, create a new manager
with the credentials. For managed identity, workload federation, certificates,
or another provider, inject a `TokenProvider` with `get_token(scope)`.

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
delegated provider/service identity. Review the
[0.1.x migration note](docs/migration-0.1.md) before upgrading from 0.0.x.
