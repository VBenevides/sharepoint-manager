class SPFolderNotFound(Exception):
    """Sharepoint folder not found"""


class SPFolderNotEmpty(Exception):
    """Sharepoint folder was not empty"""


class SPFileNotFound(Exception):
    """Sharepoint file was not found"""


class SPValidationError(ValueError):
    """A caller supplied an unsafe or invalid operation value."""


class SPDriveNotFound(SPValidationError):
    """The requested document library does not exist."""


class SPUnauthorizedTarget(SPValidationError):
    """A resolved resource is outside the manager's configured boundary."""


class SPAmbiguousWriteError(RuntimeError):
    """A write may have reached Graph but its final outcome is unknown."""

    def __init__(self, upload_url: str, cause: Exception | None = None) -> None:
        super().__init__("Upload outcome is ambiguous")
        self.upload_url = upload_url
        self.cause = cause
