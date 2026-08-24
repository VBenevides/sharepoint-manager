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
from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPCollectionPage,
    SPDeltaPage,
    SPDeletedItem,
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
    SPFileNotFound,
    SPFileIntegrityError,
    SPFolderNotEmpty,
    SPFolderNotFound,
    SPGraphError,
    SPThrottledError,
    SPValidationError,
)
from .utils import QuickXorHash

__all__ = [
    "ClientCredential",
    "OperationPolicy",
    "QuickXorHash",
    "SPDeletedItem",
    "SPCollectionPage",
    "SPDeltaPage",
    "SPFile",
    "SPFileNotFound",
    "SPAmbiguousWriteError",
    "SPAuthenticationError",
    "SPAuthorizationError",
    "SPConflictError",
    "SPDeadlineExceeded",
    "SPDriveNotFound",
    "SPFileIntegrityError",
    "SPFolder",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
    "SPGraphError",
    "SharepointManager",
    "TokenProvider",
    "UserDelegatedCredential",
    "SPThrottledError",
    "SPValidationError",
]
