__version__ = "0.0.8"

# Import core components
from .exceptions import SPFileNotFound, SPFolderNotEmpty, SPFolderNotFound
from .dataclasses import (
    ClientCredential,
    OperationPolicy,
    SPDeletedItem,
    SPFile,
    SPFolder,
    TokenProvider,
    UserDelegatedCredential,
)
from .core import SharepointManager
from .utils import QuickXorHash

__all__ = [
    "SharepointManager",
    "SPFile",
    "SPFolder",
    "ClientCredential",
    "OperationPolicy",
    "SPDeletedItem",
    "TokenProvider",
    "UserDelegatedCredential",
    "SPFileNotFound",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
    "QuickXorHash",
]
