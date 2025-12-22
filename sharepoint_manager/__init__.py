__version__ = "0.0.5"

# Import core components
from .exceptions import SPFileNotFound, SPFolderNotEmpty, SPFolderNotFound
from .dataclasses import SPFile, SPFolder, ClientCredential, UserDelegatedCredential
from .core import SharepointManager, SharepointManagerUrl

__all__ = [
    "SharepointManager",
    "SharepointManagerUrl",
    "SPFile",
    "SPFolder",
    "ClientCredential",
    "UserDelegatedCredential",
    "SPFileNotFound",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
]
