"""Pure URL and host validation shared by the sync and async clients."""

import base64
from urllib.parse import urlparse

from .exceptions import SPValidationError

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
