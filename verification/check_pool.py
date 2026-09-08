import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import TestCase
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager import (
    ClientCredential,
    SharepointManager,
    SharepointManagerPool,
    SPValidationError,
    get_sharepoint_manager,
)
from sharepoint_manager.urls import validate_sharepoint_url


def main() -> None:
    check = TestCase()
    credentials = ClientCredential("client", "secret")
    site = "https://tenant.sharepoint.com/sites/one"
    team = "https://tenant.sharepoint.com/teams/two"
    file_url = f"{site}/Shared%20Documents/file.txt"
    browser_url = (
        f"{site}/Shared%20Documents/Forms/AllItems.aspx"
        "?id=%2Fsites%2Fone%2FShared%20Documents%2Ffile.txt"
    )
    managers = [Mock(spec=SharepointManager), Mock(spec=SharepointManager)]
    with patch(
        "sharepoint_manager.pool.SharepointManager", side_effect=managers
    ) as create:
        pool = SharepointManagerPool(credentials, document_folder_name="Documents")
        create.assert_not_called()
        for url in (
            "http://tenant.sharepoint.com/sites/one/file.txt",
            "https://example.com/sites/one/file.txt",
            "https://tenant.sharepoint.com.example.com/sites/one/file.txt",
        ):
            with check.assertRaises(SPValidationError):
                pool.for_url(url)
        sharing_url = "https://tenant.sharepoint.com/:x:/s/one/sharing-token"
        validate_sharepoint_url(sharing_url)
        with check.assertRaisesRegex(ValueError, "site name"):
            pool.for_url(sharing_url)
        create.assert_not_called()

        assert pool.for_url(file_url) is managers[0]
        assert pool.for_url(browser_url) is managers[0]
        redirect_url = (
            "https://tenant.sharepoint.com/:x:/r/sites/one/Shared%20Documents/file.xlsx"
        )
        assert pool.for_url(redirect_url) is managers[0]
        assert pool.for_url(f"{team}/Shared%20Documents/file.txt") is managers[1]
        assert pool.for_url(team) is managers[1]
        assert get_sharepoint_manager(pool, browser_url) is managers[0]
        assert get_sharepoint_manager(managers[1], file_url) is managers[1]
        with check.assertRaisesRegex(ValueError, "credentials"):
            get_sharepoint_manager(None, file_url)
        assert create.call_args_list == [
            call(
                sharepoint_site_url=url,
                credentials=credentials,
                document_folder_name="Documents",
            )
            for url in (site, team)
        ]
        assert all(
            args.kwargs["credentials"] is credentials for args in create.call_args_list
        )

        pool.close()
        for manager in managers:
            manager.close.assert_called_once_with()
        pool.close()
        for manager in managers:
            manager.close.assert_called_once_with()
        for url in (file_url, "https://tenant.sharepoint.com/sites/new/file.txt"):
            with check.assertRaises(RuntimeError):
                pool.for_url(url)
        with check.assertRaises(RuntimeError):
            get_sharepoint_manager(pool, browser_url)
        assert create.call_count == 2

    with patch(
        "sharepoint_manager.pool.SharepointManager",
        side_effect=lambda **kwargs: Mock(spec=SharepointManager),
    ) as create:
        pool = SharepointManagerPool(credentials)
        workers = 8
        ready = Barrier(workers)

        def lookup(index):
            ready.wait(timeout=5)
            return pool.for_url(file_url if index % 2 else browser_url)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(lookup, range(workers)))
        assert all(manager is results[0] for manager in results)
        create.assert_called_once_with(
            sharepoint_site_url=site,
            credentials=credentials,
            document_folder_name=None,
        )
        pool.close()
        results[0].close.assert_called_once_with()


if __name__ == "__main__":
    main()
