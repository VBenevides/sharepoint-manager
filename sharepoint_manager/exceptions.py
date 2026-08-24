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
