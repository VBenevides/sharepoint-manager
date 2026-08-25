class SPError(Exception):
    """Stable public error carrying transport diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.retryable = retryable
        self.cause = cause


class SPValidationError(SPError, ValueError):
    """A caller supplied an unsafe or invalid operation value."""


class SPAuthenticationError(SPError):
    """Token acquisition failed."""


class SPAuthorizationError(SPError):
    """Graph denied access to a resource."""


class SPThrottledError(SPError):
    """Graph throttled a request after the retry budget was exhausted."""


class SPConflictError(SPError):
    """Graph rejected a conflicting write."""


class SPGraphError(SPError):
    """A Graph HTTP request failed."""


class SPNotFoundError(SPError):
    """A requested Graph resource was not found."""


class SPDeadlineExceeded(SPError):
    """An operation exceeded its configured deadline."""


class SPFolderNotFound(SPNotFoundError):
    """SharePoint folder not found."""


class SPFolderNotEmpty(SPError):
    """SharePoint folder was not empty."""


class SPFileNotFound(SPNotFoundError):
    """SharePoint file was not found."""


class SPDriveNotFound(SPNotFoundError):
    """The requested document library does not exist."""


class SPUnauthorizedTarget(SPValidationError):
    """A resolved resource is outside the manager's configured boundary."""


class SPAmbiguousWriteError(SPError):
    """A write may have reached Graph but its final outcome is unknown."""

    def __init__(
        self,
        _upload_url: str | None = None,
        _cause: Exception | None = None,
    ) -> None:
        super().__init__("Upload outcome is ambiguous", retryable=True)


class SPFileIntegrityError(SPError):
    """Downloaded content did not match its Graph-provided hash."""
