"""Lazy SharePoint manager pooling by source site."""

import re
from threading import Lock

from .core import SharepointManager
from .dataclasses import ClientCredential, UserDelegatedCredential
from .urls import validate_sharepoint_url


class SharepointManagerPool:
    """Own one manager per source site with shared credentials and library."""

    def __init__(
        self,
        credentials: ClientCredential | UserDelegatedCredential,
        document_folder_name: str | None = None,
    ) -> None:
        """Initialize an empty pool with the configured credentials and library.

        Parameters
        ----------
        credentials : ClientCredential or UserDelegatedCredential
            Credentials shared by managers created by the pool.
        document_folder_name : str, optional
            Document library name passed to each created manager.
        """
        self.credentials = credentials
        self.document_folder_name = document_folder_name
        self._managers: dict[str, SharepointManager] = {}
        self._lock = Lock()
        self._closed = False

    def for_url(self, url: str) -> SharepointManager:
        """Return the cached manager for an approved SharePoint source site.

        Parameters
        ----------
        url : str
            SharePoint source URL used to identify the site.

        Returns
        -------
        SharepointManager
            The lazily created manager for the source site.
        """
        parsed = validate_sharepoint_url(url)
        site = re.search(r"/(?:sites|teams)/[^/]+", parsed.path)
        if site is None:
            raise ValueError("SharePoint source URL must include a site name")
        site_url = f"{parsed.scheme}://{parsed.netloc}{site.group()}"
        # ponytail: serialize site initialization; use per-site locks if contention matters.
        with self._lock:
            if self._closed:
                raise RuntimeError("SharePoint managers are closed")
            if site_url not in self._managers:
                self._managers[site_url] = SharepointManager(
                    sharepoint_site_url=site_url,
                    credentials=self.credentials,
                    document_folder_name=self.document_folder_name,
                )
            return self._managers[site_url]

    def close(self) -> None:
        """Close all cached managers and prevent further lookups."""
        with self._lock:
            self._closed = True
            for manager in self._managers.values():
                manager.close()
            self._managers.clear()


def get_sharepoint_manager(
    manager: SharepointManager | SharepointManagerPool | None, source_url: str
) -> SharepointManager:
    """Resolve a pooled or direct manager, requiring SharePoint credentials."""
    if isinstance(manager, SharepointManagerPool):
        return manager.for_url(source_url)
    if manager is None:
        raise ValueError("SharePoint credentials are required for SharePoint sources")
    return manager
