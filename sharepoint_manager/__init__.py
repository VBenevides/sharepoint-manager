__version__ = "0.0.3"

# Import core components
from .exceptions import SPFileNotFound, SPFolderNotEmpty, SPFolderNotFound
from .dataclasses import SPFile, SPFolder, ClientCredential
from .core import SharepointManager

__all__ = [
    "SharepointManager",
    "SPFile",
    "SPFolder",
    "ClientCredential",
    "SPFileNotFound",
    "SPFolderNotEmpty",
    "SPFolderNotFound",
]
