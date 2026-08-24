from dataclasses import dataclass, fields, field
from typing import Any, Protocol
from .utils import camel_to_snake
class TokenProvider(Protocol):
    """Reusable token contract compatible with managed identity providers."""

    def get_token(self, scope: str) -> Any:
        """Return a token string or an object with ``token``/``expires_on``."""
@dataclass(repr=False)
class ClientCredential:
    client_id: str
    client_secret: str = field(repr=False)

    def __repr__(self) -> str:
        return f"ClientCredential(client_id={self.client_id!r}, client_secret=***)"


@dataclass(repr=False)
class UserDelegatedCredential:
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


def dataclass_from_dict(cls, data: dict[str, Any], extra_mapping: dict[str, str] | None = None):
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
    context: str = ""
    folder: dict[str, Any] = field(default_factory=dict)

    @property
    def child_count(self) -> int:
        return self.folder.get("childCount", 0)

    @property
    def is_root(self) -> bool:
        return self.name == ""

    @property
    def relative_url(self) -> str:
        """
        The most common url format is https://tenant.sharepoint.com/sites/site_name/documents_folder/folder1/folder2
        We want to get everything after the documents folder: folder1/folder2
        """

        # include "/" because root url ends with /documents_folder
        parts = (self.web_url + "/").split("/")
        # skip sites, site_name, documents_folder
        try:
            id_start = parts.index("sites") + 3
        except ValueError:
            try:
                id_start = parts.index("teams") + 3
            except ValueError:
                return ""
        relative_url = "/".join(parts[id_start:])
        if relative_url and relative_url[-1] == "/":
            relative_url = relative_url[:-1]
        return relative_url

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPFolder":
        if "root" in data:
            data = {**data, "name": ""}
        return dataclass_from_dict(cls, data, {"@odata.context": "context"})


@dataclass
class SPFile(SPObject):
    download_url: str = ""
    file: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPFile":
        return dataclass_from_dict(cls, data, {"@microsoft.graph.downloadUrl": "download_url"})
