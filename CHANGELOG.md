# Changelog

## [Unreleased]

- Removed the scheduled live-tenant staging workflow and smoke harness while
  retaining the protected release checks.
- Added branch and pull-request security scans for dependencies, licenses, SBOM
  generation, and secrets; CodeQL now declares the same triggers explicitly.
- Updated the security toolchain for Python 3.14 dependency resolution.

## [0.1.0] - 2026-08-24

- Intentional breaking release with explicit sync and native async clients.
- Added URL-targeted file and recursive folder transfers with boundary checks.
- Added warned service-account username/password bootstrap with silent token
  reuse through the in-memory MSAL cache; passwords are not persisted by the
  package.
- Removed mutable current-folder state and materialized delta compatibility wrappers.
- Enforced zero-error Ruff checks for contributors and CI.
