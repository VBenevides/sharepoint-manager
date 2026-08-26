# Changelog

## [0.1.2] - 2026-08-25

### Features

- Added six offline examples covering client credentials, legacy delegated
  credentials, synchronous and asynchronous filesystem workflows, and URL
  workflows.
- Added NumPy-style documentation for exported classes and methods, plus
  doctest and signature checks, and sync/async contract checks.

### Bugfixes

- Made async credential authentication use an explicit tenant authority and
  aligned async HTTP 401 handling with the synchronous client.
- Enforced object download boundaries and redacted provider, capability URL,
  and transport failure details from ordinary exceptions.
- Removed redundant synchronous folder lookups while preserving concurrent
  creator conflict recovery.
- Enforced the reviewed license deny-list policy.

### Other

- Upgraded the isolated build frontend and clarified the async event-loop
  contract for local filesystem work.
- Replaced literal credential values in documentation with environment-backed
  examples.

### Breaking Changes

- Async credential-based managers now require an explicit `tenant_id`; injected
  token providers remain exempt.

## [0.1.1] - 2026-08-25

### Features

- Added direct Graph content uploads for small files while retaining resumable
  sessions for larger files.

### Bugfixes

- Enforced configured site and drive boundaries for async URL operations.
- Accepted SharePoint browser location URLs as well as sharing links for URL
  operations, including composite Graph site IDs.
- Preserved safe stale-sharing-link details in Graph errors.
- Capped streamed download bytes before writing oversized responses.
- Corrected retry behavior for non-idempotent requests and resumable uploads.
- Bounded recursive pagination and closed discarded responses.
- Closed temporary download descriptors on setup failures.
- Kept streaming downloads within the configured concurrency limit.

### Others

- Added a local async transfer-lag benchmark with explicit network-measurement
  limitations.
- Added minimum-profile dependency auditing.

### Breaking Changes

- Raised the minimum supported MSAL version to 1.37.
- Async URL operations now reject resources outside the configured site/drive
  boundary.

## [Project / Repository] - 2026-08-24

- Removed the scheduled live-tenant staging workflow and smoke harness while
  retaining the protected release checks.
- Added branch and pull-request security scans for dependencies, licenses, SBOM
  generation, and secrets; CodeQL now declares the same triggers explicitly.
- Updated the security toolchain for Python 3.14 dependency resolution.
- Corrected CycloneDX SBOM validation to check CycloneDX fields.
- Updated Gitleaks pull-request authentication and its GitHub Actions runtime.
- Scoped Ruff's import-position check for verification bootstrap scripts.

## [0.1.0] - 2026-08-24

- Intentional breaking release with explicit sync and native async clients.
- Added URL-targeted file and recursive folder transfers with boundary checks.
- Added warned service-account username/password bootstrap with silent token
  reuse through the in-memory MSAL cache; passwords are not persisted by the
  package.
- Removed mutable current-folder state and materialized delta compatibility wrappers.
- Enforced zero-error Ruff checks for contributors and CI.
