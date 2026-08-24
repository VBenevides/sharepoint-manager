__version__ = "0.0.8"

# Import core components
from .core import SharepointManager
from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPDeletedItem,
    SPFile,
    SPFolder,
    TokenProvider,
    UserDelegatedCredential,
)
from .exceptions import (
    SPAmbiguousWriteError,
    SPFileNotFound,
    SPFolderNotEmpty,
    SPFolderNotFound,
)
from .utils import QuickXorHash

__all__ = [
    "ClientCredential",
    "OperationPolicy",
    "QuickXorHash",
    "SPDeletedItem",
    "SPFile",
    "SPFileNotFound",
    "SPAmbiguousWriteError",
    "SPFolder",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
    "SharepointManager",
    "TokenProvider",
    "UserDelegatedCredential",
]
