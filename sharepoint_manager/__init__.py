from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("sharepoint_manager")
except PackageNotFoundError:
    __version__ = (
        Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip()
    )

# Import core components
from .core import SharepointManager
from .async_core import AsyncSharepointManager
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

__all__ = [
    "ClientCredential",
    "AsyncSharepointManager",
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
