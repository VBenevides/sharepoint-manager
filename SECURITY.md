# Security policy

Do not open a public issue with credentials, bearer tokens, capability URLs,
tenant identifiers, file names, permission members, or customer content.

Report suspected vulnerabilities privately to the repository maintainers and
include a minimal reproduction, affected version, impact, and a safe contact
method. Remove secrets and proprietary data before sharing logs or patches.

The package supports both confidential app-registration credentials and an
explicit service-account username/password flow. The latter requires public
client flows and emits a warning because ROPC does not support MFA or
Conditional Access. Do not persist the service-account or end-user password:
MSAL uses it only for the initial token request, then obtains tokens silently
from its in-memory cache. A cache miss requires the caller to provide the
credentials again.

The package applies HTTPS and host validation, configured SharePoint
site/drive boundaries, finite transfer and traversal limits, safe archive/path
handling, typed redacted errors, and privacy-safe normal logging. Consumers
must still restrict Entra grants, protect client secrets, control outbound
proxies/DNS, and treat Graph capability URLs as short-lived secrets.

Live tenant tests must use a dedicated least-privilege test site and tagged
disposable content. Keep them outside normal CI and release workflows; see
[deployment permissions](docs/permissions.md).
