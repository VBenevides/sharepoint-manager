import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Import core components
from .async_core import AsyncSharepointManager
from .core import SharepointManager
from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPCollectionPage,
    SPDeletedItem,
    SPDeltaPage,
    SPFile,
    SPFolder,
    TokenProvider,
    UserDelegatedCredential,
)
from .exceptions import (
    SPAmbiguousWriteError,
    SPAuthenticationError,
    SPAuthorizationError,
    SPConflictError,
    SPDeadlineExceeded,
    SPDriveNotFound,
    SPFileIntegrityError,
    SPFileNotFound,
    SPFolderNotEmpty,
    SPFolderNotFound,
    SPGraphError,
    SPNotFoundError,
    SPThrottledError,
    SPValidationError,
)
from .utils import QuickXorHash

_logger = logging.getLogger(__name__)
if _logger.level == logging.NOTSET:
    _logger.setLevel(logging.WARNING)
_logger.addHandler(logging.NullHandler())

try:
    __version__ = version("sharepoint_manager")
except PackageNotFoundError:
    __version__ = (
        Path(__file__)
        .parents[1]
        .joinpath("VERSION")
        .read_text(encoding="utf-8")
        .strip()
    )

__all__ = [
    "AsyncSharepointManager",
    "ClientCredential",
    "OperationPolicy",
    "QuickXorHash",
    "SPAmbiguousWriteError",
    "SPAuthenticationError",
    "SPAuthorizationError",
    "SPCollectionPage",
    "SPConflictError",
    "SPDeadlineExceeded",
    "SPDeletedItem",
    "SPDeltaPage",
    "SPDriveNotFound",
    "SPFile",
    "SPFileIntegrityError",
    "SPFileNotFound",
    "SPFolder",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
    "SPGraphError",
    "SPNotFoundError",
    "SPThrottledError",
    "SPValidationError",
    "SharepointManager",
    "TokenProvider",
    "UserDelegatedCredential",
]
