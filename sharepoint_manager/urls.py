"""Pure URL and host validation shared by the sync and async clients."""

import base64
import re
from urllib.parse import unquote, urlparse

from .exceptions import SPUnauthorizedTarget, SPValidationError

GRAPH_HOSTS = frozenset(
    {
        "graph.microsoft.com",
        "graph.microsoft.us",
        "dod-graph.microsoft.us",
        "graph.microsoft.de",
        "microsoftgraph.chinacloudapi.cn",
    }
)
MICROSOFT_CAPABILITY_SUFFIXES = (
    ".sharepoint.com",
    ".sharepoint.us",
    ".sharepoint.de",
    ".sharepoint.cn",
    ".sharepoint-mil.us",
    ".sharepoint-df.com",
    ".1drv.com",
)
SHAREPOINT_SUFFIXES = tuple(
    suffix for suffix in MICROSOFT_CAPABILITY_SUFFIXES if suffix != ".1drv.com"
)
_LOCATION_REDIRECT_RE = re.compile(r"^/:[^/]+:/r(?P<path>/.*)$")


def validate_graph_url(url: str, graph_host: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.hostname != graph_host
        or parsed.port not in (None, 443)
    ):
        raise SPValidationError(
            "Authenticated requests require the configured HTTPS Graph host"
        )


def validate_capability_url(url: str, graph_host: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
        or not (
            host == graph_host
            or any(host.endswith(suffix) for suffix in MICROSOFT_CAPABILITY_SUFFIXES)
        )
    ):
        raise SPValidationError(
            "Capability URLs must use an approved HTTPS Microsoft host"
        )


def validate_sharepoint_url(url: str):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
        or not any(host.endswith(suffix) for suffix in SHAREPOINT_SUFFIXES)
        or not ("/sites/" in parsed.path or "/teams/" in parsed.path)
    ):
        raise SPValidationError("SharePoint URLs must use an approved HTTPS site host")
    return parsed


def share_id(url: str) -> str:
    return "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def sharepoint_location_path(
    url: str, configured_site_url: str, drive_url_name: str
) -> str | None:
    """Return the drive-relative path from a browser SharePoint URL."""
    parsed = urlparse(url)
    configured = urlparse(configured_site_url)
    if parsed.netloc.lower() != configured.netloc.lower():
        return None
    path = unquote(parsed.path).rstrip("/") or "/"
    site_path = unquote(configured.path).rstrip("/") or "/"
    redirect = _LOCATION_REDIRECT_RE.match(path)
    if redirect:
        path = redirect.group("path").rstrip("/") or "/"
    if path != site_path and not path.startswith(f"{site_path}/"):
        return None

    relative = path[len(site_path) :].strip("/")
    drive_name = unquote(drive_url_name).strip("/")
    if relative == drive_name:
        return ""
    prefix = f"{drive_name}/"
    if not relative.startswith(prefix):
        raise SPUnauthorizedTarget(
            "Resolved URL is outside the configured SharePoint drive"
        )
    return relative[len(prefix) :]


def safe_graph_error_detail(response) -> str | None:
    """Expose only the known safe Graph detail used for stale sharing links."""
    try:
        payload = response.json()
        message = payload.get("error", {}).get("message")
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(message, str):
        return None
    normalized = " ".join(message.split())
    if normalized.rstrip(".").lower() in {
        "sharing link no longer available",
        "the sharing link is no longer available",
    }:
        return normalized
    return None
