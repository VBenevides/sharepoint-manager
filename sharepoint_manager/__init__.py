__version__ = "0.0.8"

# Import core components
from .exceptions import SPFileNotFound, SPFolderNotEmpty, SPFolderNotFound
from .dataclasses import (
    ClientCredential,
    OperationPolicy,
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
    "TokenProvider",
    "UserDelegatedCredential",
    "SPFileNotFound",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
    "QuickXorHash",
]
