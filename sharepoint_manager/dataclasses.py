import math
from dataclasses import dataclass, field, fields
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .utils import camel_to_snake


class TokenProvider(Protocol):
    """Reusable token contract compatible with managed identity providers."""

    def get_token(self, scope: str) -> Any:
        """Return a token string or an object with token metadata.

        Parameters
        ----------
        scope : str
            Resource scope requested by the manager.

        Returns
        -------
        Any
            Token string, mapping, or provider token object.
        """


@dataclass(frozen=True)
class OperationPolicy:
    """Finite resource and retry budgets for one manager."""

    max_file_bytes: int = 10 * 1024**3
    max_total_bytes: int = 100 * 1024**3
    max_disk_bytes: int = 100 * 1024**3
    max_archive_bytes: int = 10 * 1024**3
    max_expanded_bytes: int = 100 * 1024**3
    max_items: int = 100_000
    max_depth: int = 64
    max_pages: int = 1_000
    max_concurrency: int = 1
    wall_clock_seconds: float = 3_600.0
    max_retry_attempts: int = 5
    max_retry_after_seconds: float = 60.0
    allow_capability_redirects: bool = False
    redact_logs: bool = True

    def __post_init__(self) -> None:
        integer_fields = (
            "max_file_bytes",
            "max_total_bytes",
            "max_disk_bytes",
            "max_archive_bytes",
            "max_expanded_bytes",
            "max_items",
            "max_depth",
            "max_pages",
            "max_concurrency",
            "max_retry_attempts",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("wall_clock_seconds", "max_retry_after_seconds"):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number")
        if not isinstance(self.allow_capability_redirects, bool):
            raise TypeError("allow_capability_redirects must be a boolean")
        if not isinstance(self.redact_logs, bool):
            raise TypeError("redact_logs must be a boolean")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("max_file_bytes cannot exceed max_total_bytes")
        if self.max_file_bytes > self.max_disk_bytes:
            raise ValueError("max_file_bytes cannot exceed max_disk_bytes")
        if self.max_archive_bytes > self.max_expanded_bytes:
            raise ValueError("max_archive_bytes cannot exceed max_expanded_bytes")


@dataclass(repr=False)
class ClientCredential:
    """Application credential for Microsoft Graph.

    Parameters
    ----------
    client_id : str
        Application (client) identifier.
    client_secret : str
        Application secret. It is excluded from representations.
    """

    client_id: str
    client_secret: str = field(repr=False)

    def __repr__(self) -> str:
        return f"ClientCredential(client_id={self.client_id!r}, client_secret=***)"


@dataclass(repr=False)
class UserDelegatedCredential:
    """Legacy username/password credential for delegated authentication.

    Parameters
    ----------
    client_id : str
        Public application identifier.
    username : str
        User principal name.
    password : str
        Password used only for the legacy ROPC bootstrap.
    """

    client_id: str
    username: str
    password: str = field(repr=False)

    def __repr__(self) -> str:
        return (
            f"UserDelegatedCredential(client_id={self.client_id!r}, "
            f"username={self.username!r}, password=***)"
        )


@dataclass
class SPObject:
    """Common metadata returned for a Microsoft Graph drive item.

    Parameters
    ----------
    id : str
        Stable Graph item identifier.
    name : str, default=""
        Display name of the item.
    parent_reference : dict[str, Any], default_factory=dict
        Graph parent and boundary metadata.
    """

    id: str
    name: str = ""
    created_datetime: str | None = None
    last_modified_datetime: str | None = None
    parent_reference: dict[str, Any] = field(default_factory=dict)
    web_url: str = ""
    file_system_info: dict[str, Any] = field(default_factory=dict)
    size: int = 0
    created_by: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_modified_by: dict[str, dict[str, Any]] = field(default_factory=dict)
    shared: dict[str, Any] = field(default_factory=dict)
    c_tag: str = ""
    e_tag: str = ""


def dataclass_from_dict(
    cls, data: dict[str, Any], extra_mapping: dict[str, str] | None = None
):
    valid_fields = {f.name for f in fields(cls)}
    # Work on a shallow copy to avoid mutating the caller's dict.
    working = dict(data)

    if extra_mapping:
        for k, v in extra_mapping.items():
            if k in working:
                working[v] = working[k]

    normalized = {}
    for k, v in working.items():
        snake = camel_to_snake(k)
        if snake in valid_fields:
            normalized[snake] = v
    return cls(**normalized)


@dataclass
class SPFolder(SPObject):
    """SharePoint folder metadata and derived path helpers.

    Parameters
    ----------
    id, name, parent_reference : object fields
        Common :class:`SPObject` metadata.
    context : str, default=""
        Optional Graph context value.
    folder : dict[str, Any], default_factory=dict
        Raw Graph folder metadata.
    """

    context: str = ""
    folder: dict[str, Any] = field(default_factory=dict)

    @property
    def child_count(self) -> int:
        """Return Graph's reported number of direct children."""
        return self.folder.get("childCount", 0)

    @property
    def is_root(self) -> bool:
        """Return whether this object represents the drive root."""
        return self.name == ""

    @property
    def relative_url(self) -> str:
        """
        The most common url format is https://tenant.sharepoint.com/sites/site_name/documents_folder/folder1/folder2
        We want to get everything after the documents folder: folder1/folder2
        """

        parent_path = self.parent_reference.get("path", "")
        if isinstance(parent_path, str) and "root:" in parent_path:
            relative = parent_path.split("root:", 1)[1].strip("/")
            parts = [unquote(part) for part in relative.split("/") if part]
            if self.name:
                parts.append(self.name)
            return "/".join(parts)

        # Fallback for older Graph payloads without parentReference.path.
        parts = [
            unquote(part) for part in urlsplit(self.web_url).path.split("/") if part
        ]
        # skip sites, site_name, documents_folder
        try:
            id_start = parts.index("sites") + 3
        except ValueError:
            try:
                id_start = parts.index("teams") + 3
            except ValueError:
                return ""
        return "/".join(parts[id_start:])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPFolder":
        """Build folder metadata from a Graph response.

        Parameters
        ----------
        data : dict[str, Any]
            Graph drive-item payload.

        Returns
        -------
        SPFolder
            Normalized folder metadata.
        """
        if "root" in data:
            data = {**data, "name": ""}
        return dataclass_from_dict(cls, data, {"@odata.context": "context"})


@dataclass
class SPFile(SPObject):
    """SharePoint file metadata and its capability download URL.

    Parameters
    ----------
    id, name, parent_reference : object fields
        Common :class:`SPObject` metadata.
    download_url : str, default=""
        Short-lived Graph download capability URL.
    file : dict[str, Any], default_factory=dict
        Raw Graph file metadata and hashes.
    """

    download_url: str = field(default="", repr=False)
    file: dict[str, Any] = field(default_factory=dict)

    @property
    def quick_xor_hash(self) -> str:
        """Return the Graph QuickXorHash value, or an empty string."""
        hashes = self.file.get("hashes", {})
        return str(hashes.get("quickXorHash", "")) if isinstance(hashes, dict) else ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPFile":
        """Build file metadata from a Graph response.

        Parameters
        ----------
        data : dict[str, Any]
            Graph drive-item payload.

        Returns
        -------
        SPFile
            Normalized file metadata.
        """
        return dataclass_from_dict(
            cls, data, {"@microsoft.graph.downloadUrl": "download_url"}
        )


@dataclass(frozen=True)
class SPDeletedItem:
    """A Graph delta tombstone preserved without a follow-up fetch."""

    id: str
    name: str = ""
    parent_reference: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPDeletedItem":
        """Build a delta tombstone from a Graph response.

        Parameters
        ----------
        data : dict[str, Any]
            Graph deleted-item payload.

        Returns
        -------
        SPDeletedItem
            Normalized tombstone metadata.
        """
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            parent_reference=dict(data.get("parentReference", {})),
            metadata=dict(data),
        )


@dataclass(frozen=True)
class SPCollectionPage:
    """One lazy Graph collection page."""

    items: tuple[dict[str, Any], ...]
    next_link: str | None = None


@dataclass(frozen=True)
class SPDeltaPage:
    """One lazy delta page and its caller-owned checkpoint links."""

    files: tuple[SPFile, ...] = ()
    folders: tuple[SPFolder, ...] = ()
    deleted: tuple[SPDeletedItem, ...] = ()
    next_link: str | None = None
    delta_link: str | None = None
