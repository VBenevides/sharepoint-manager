# SharePoint Manager 0.1.x Contract

This is an intentional breaking release from `0.0.x`. The 0.1.x contract is
the supported surface; compatibility aliases, deprecated facades, and ambient
manager state are not part of the design.

See [the migration note](migration-0.1.md) for the removed workflows and their
replacements.

## Clients and authentication

- `SharepointManager` provides bounded synchronous operations.
- `AsyncSharepointManager` provides native asyncio upload and download
  operations.
- App registration uses a confidential MSAL client with a client ID and
  client secret.
- Service-account user authentication uses an explicitly configured public
  MSAL client with a client ID, username, and password. The app registration
  must allow public client flows.
- The service-account password is used only for the initial username/password
  token request. The manager then uses MSAL's in-memory token cache and
  `acquire_token_silent()` for later access and refresh. A cache miss requires
  the caller to supply credentials again.
- Service-account authentication emits a visible security warning when used.
  The warning explains that ROPC is deprecated, does not support MFA or
  Conditional Access, and handles a reusable password.
- Passwords, tokens, capability URLs, and customer content are never written
  to logs, telemetry, representations, exceptions, or persistent files.

## Operations

- Every operation receives an explicit path, URL, or resolved SharePoint
  object. There is no current-folder or working-directory state on the
  manager.
- Path and URL workflows cover metadata, listing/traversal, creation,
  deletion, file upload/download, folder upload/download, and the required
  permission reads.
- Sync and async clients share policy validation, resource-boundary checks,
  model mapping, error semantics, and transfer integrity rules.
- Recursive operations enforce one cumulative byte, item, page, depth, and
  wall-clock budget.

## Safety and operations

- Site, drive, Graph-host, SharePoint-host, and capability-URL boundaries are
  validated before use.
- Downloads use temporary sibling files, size/hash validation, and atomic
  replacement. Uploads use bounded retries and integrity-aware transfer
  handling.
- Threaded sync operations use bounded parallelism with explicit operation
  targets and coordinated shutdown. Async network requests use non-blocking
  I/O and cancellation-safe cleanup; local filesystem reads, writes, and
  hashing remain synchronous, so callers can offload a complete filesystem
  workflow with `asyncio.to_thread` when event-loop latency matters.
- Structured info/error logs and telemetry carry a redacted correlation ID,
  operation, elapsed time, status, and safe failure details.

## Removed from 0.1.x

- `set_folder`, `cwd`, and any other mutable current-folder facade.
- Compatibility wrappers retained only to preserve 0.0.x names or behavior.
- Automatic fallback from app authentication to username/password
  authentication.
