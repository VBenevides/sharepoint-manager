# Security policy

Do not open a public issue with credentials, bearer tokens, capability URLs,
tenant identifiers, file names, permission members, or customer content.

Report suspected vulnerabilities privately to the repository maintainers and
include a minimal reproduction, affected version, impact, and a safe contact
method. Remove secrets and proprietary data before sharing logs or patches.

The package applies HTTPS and host validation, configured SharePoint
site/drive boundaries, finite transfer and traversal limits, safe archive/path
handling, typed redacted errors, and privacy-safe normal logging. Consumers
must still restrict Entra grants, protect client secrets, control outbound
proxies/DNS, and treat Graph capability URLs as short-lived secrets.

Live tenant tests must use a dedicated least-privilege staging site and tagged
disposable content. See the scheduled staging workflow and
[deployment permissions](docs/permissions.md).
