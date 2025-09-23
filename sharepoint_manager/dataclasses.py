from dataclasses import dataclass, fields, field
from typing import Any

from .utils import camel_to_snake


@dataclass
class ClientCredential:
    client_id: str
    client_secret: str


@dataclass
class SPObject:
    created_datetime: str
    id: str
    last_modified_datetime: str
    name: str
    parent_reference: dict[str, str]
    web_url: str
    file_system_info: dict[str, str]
    size: int
    created_by: dict[str, dict[str, str]] = field(default_factory=dict)
    last_modified_by: dict[str, dict[str, str]] = field(default_factory=dict)
    shared: dict[str, str] = field(default_factory=dict)
    c_tag: str = ""
    e_tag: str = ""


def dataclass_from_dict(cls, data: dict[str, Any], extra_mapping: dict[str, str] | None = None):
    valid_fields = {f.name for f in fields(cls)}
    normalized = {}

    if extra_mapping:
        for k, v in extra_mapping.items():
            if k in data:
                data[v] = data[k]

    for k, v in data.items():
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
        id_start = parts.index("sites") + 3
        relative_url = "/".join(parts[id_start:])
        if relative_url and relative_url[-1] == "/":
            relative_url = relative_url[:-1]
        return relative_url

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPFolder":
        if "root" in data:
            data["name"] = ""
        return dataclass_from_dict(cls, data, {"@odata.context": "context"})


@dataclass
class SPFile(SPObject):
    download_url: str = ""
    file: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SPFile":
        return dataclass_from_dict(cls, data, {"@microsoft.graph.downloadUrl": "download_url"})
