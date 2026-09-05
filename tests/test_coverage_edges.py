import asyncio
import threading
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from sharepoint_manager import (
    AsyncSharepointManager,
    ClientCredential,
    OperationPolicy,
    SharepointManager,
    SPFile,
    SPFolder,
    UserDelegatedCredential,
)
from sharepoint_manager.core import SharepointManagerBase
from sharepoint_manager.exceptions import (
    SPAuthenticationError,
    SPAuthorizationError,
    SPConflictError,
    SPDeadlineExceeded,
    SPGraphError,
    SPNotFoundError,
    SPThrottledError,
    SPUnauthorizedTarget,
    SPValidationError,
)
from sharepoint_manager.utils import (
    QuickXorHash,
    parse_www_authenticate,
    safe_join,
    validate_archive_members,
)


class Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self._payload

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("request failed")


class CoverageEdges(unittest.TestCase):
    def test_utility_and_dataclass_edges(self):
        digest = QuickXorHash()
        digest.update(b"x" * 161)
        self.assertEqual(len(digest.digest()), 20)
        self.assertEqual(len(digest.b64digest()), 28)
        self.assertEqual(len(digest.hexdigest()), 40)

        with self.assertRaises(ValueError):
            safe_join("/tmp", None)
        with self.assertRaises(ValueError):
            validate_archive_members([("x", 1)], 0, 10)
        for member in (("x\x00", 1), ("/x", 1), ("..", 1), ("../x", 1), ("x", -1)):
            with self.assertRaises(ValueError):
                validate_archive_members([member], 5, 10)
        with self.assertRaises(ValueError):
            validate_archive_members([("x", 11)], 5, 10)

        self.assertEqual(parse_www_authenticate(""), {})
        self.assertEqual(
            parse_www_authenticate('Bearer realm="tenant,west", ignored, error=bad'),
            {"realm": "tenant,west", "error": "bad"},
        )

        with self.assertRaises(ValueError):
            OperationPolicy(max_file_bytes=2, max_total_bytes=1)
        with self.assertRaises(ValueError):
            OperationPolicy(max_file_bytes=2, max_disk_bytes=1)
        with self.assertRaises(ValueError):
            OperationPolicy(max_archive_bytes=2, max_expanded_bytes=1)
        self.assertEqual(
            repr(ClientCredential("client", "secret")),
            "ClientCredential(client_id='client', client_secret=***)",
        )
        self.assertIn("password=***", repr(UserDelegatedCredential("c", "u", "p")))

        folder = SPFolder(
            id="folder",
            name="child",
            web_url="https://tenant/sites/site/Documents/child",
        )
        self.assertEqual(folder.child_count, 0)
        self.assertFalse(folder.is_root)
        self.assertEqual(folder.relative_url, "child")
        teams = SPFolder(
            id="folder", web_url="https://tenant/teams/team/Documents/child"
        )
        self.assertEqual(teams.relative_url, "child")
        unknown = SPFolder(id="folder", web_url="https://tenant/other/child")
        self.assertEqual(unknown.relative_url, "")

    def test_base_auth_and_telemetry_helpers(self):
        manager = object.__new__(SharepointManagerBase)
        manager.graph_host = "graph.microsoft.com"
        manager.tenant_url = "https://tenant.sharepoint.com"
        records = []
        manager.telemetry = records.append
        manager._emit_telemetry("request", status=500, outcome="failure")
        self.assertEqual(records[0]["status"], 500)

        manager.telemetry = lambda _record: (_ for _ in ()).throw(RuntimeError("bad"))
        manager._emit_telemetry("request")
        manager._ensure_token = lambda: "token"
        self.assertEqual(
            manager._hdr(json_content=True),
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        manager._ensure_token = SharepointManagerBase._ensure_token.__get__(
            manager, SharepointManagerBase
        )

        manager._request = lambda *_args, **_kwargs: Response(
            headers={"WWW-Authenticate": 'Bearer realm="tenant-id"'}
        )
        self.assertEqual(manager._get_tenant_id(), "tenant-id")
        manager._request = lambda *_args, **_kwargs: Response(
            headers={
                "WWW-Authenticate": (
                    "Bearer authorization_uri=https://login.microsoftonline.com/"
                    "12345678-1234-1234-1234-123456789abc"
                )
            }
        )
        self.assertEqual(
            manager._get_tenant_id(), "12345678-1234-1234-1234-123456789abc"
        )
        manager._request = lambda *_args, **_kwargs: Response()
        with self.assertRaises(RuntimeError):
            manager._get_tenant_id()

        manager._token_provider = types.SimpleNamespace(
            get_token=lambda _scope: {"access_token": "dict-token", "expires_on": 500}
        )
        self.assertEqual(
            manager._provider_token_result(100)["access_token"], "dict-token"
        )
        with self.assertRaises(SPAuthenticationError):
            manager._cache_acquired_token({}, 100)
        self.assertEqual(
            manager._cache_acquired_token(
                {"access_token": "cached", "expires_on": "bad", "expires_in": "bad"},
                100,
            ),
            "cached",
        )

        manager._token_lock = threading.Lock()
        manager._cached_token = ""
        manager._cached_token_expiry = 0
        manager._acquire_token = lambda _now: {
            "access_token": "fresh",
            "expires_in": 60,
        }
        self.assertEqual(manager._ensure_token(), "fresh")

        class RefreshLock:
            def __enter__(self):
                manager._cached_token = "inside-lock"
                manager._cached_token_expiry = 2**31

            def __exit__(self, *_args):
                return False

        manager._cached_token = ""
        manager._cached_token_expiry = 0
        manager._token_lock = RefreshLock()
        self.assertEqual(manager._ensure_token(), "inside-lock")

    def test_base_http_helpers(self):
        manager = object.__new__(SharepointManagerBase)
        manager.policy = OperationPolicy(max_retry_after_seconds=5)
        manager.telemetry = None
        manager._session = types.SimpleNamespace(
            request=lambda **kwargs: Response(headers={"request-id": "id"})
        )
        manager._owns_session = False
        manager._closed = False
        self.assertIsNotNone(manager._request_gate_for())
        self.assertIs(manager._request_gate_for(), manager._request_gate)
        self.assertIsNotNone(manager._request_condition_for())
        self.assertEqual(manager._request_timeout(None, 2), 2)
        self.assertEqual(manager._request_timeout((10, 1), 2), (2.0, 1.0))
        self.assertEqual(manager._request_timeout(10, 2), 2.0)
        self.assertEqual(manager._request_timeout("default", 2), "default")
        self.assertEqual(
            manager._perform_request(method="GET", url="https://graph").status_code, 200
        )

        with patch("sharepoint_manager.core.time.sleep"):
            self.assertTrue(
                manager._retry_request_exception(
                    requests.Timeout(),
                    method="GET",
                    attempt=1,
                    request_started=0,
                    retryable=True,
                    max_attempts=3,
                    deadline=10**10,
                    policy=manager.policy,
                )
            )
        response = Response(429, headers={"Retry-After": "invalid"})
        with patch("sharepoint_manager.core.time.sleep"):
            self.assertTrue(
                manager._retry_response(
                    response,
                    method="GET",
                    attempt=1,
                    max_attempts=3,
                    deadline=10**10,
                    policy=manager.policy,
                    request_id="id",
                )
            )
        self.assertGreaterEqual(
            manager._retry_after_delay(
                Response(headers={"Retry-After": "2"}), 1, manager.policy, 10**10
            ),
            0,
        )
        http_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        manager._retry_after_delay(
            Response(headers={"Retry-After": http_date}), 1, manager.policy, 10**10
        )

        timeout = requests.Timeout()
        with self.assertRaises(SPGraphError):
            manager._raise_request_failure(
                timeout, authenticated=False, deadline=10**10
            )
        timeout = requests.Timeout()
        with self.assertRaises(SPDeadlineExceeded):
            manager._raise_request_failure(timeout, authenticated=False, deadline=0)
        timeout = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            manager._raise_request_failure(timeout, authenticated=True, deadline=10**10)
        deadline_response = Response(500, headers={"request-id": "r"})
        with self.assertRaises(SPDeadlineExceeded):
            manager._raise_response_deadline(deadline_response, 0)
        redirect_response = Response(302)
        with self.assertRaises(SPValidationError):
            manager._reject_authenticated_redirect(redirect_response, True)

    def test_async_helper_edges(self):
        async def exercise():
            async_manager = AsyncSharepointManager(
                "https://tenant.sharepoint.com/sites/demo",
                token_provider=types.SimpleNamespace(
                    get_token=lambda _scope: asyncio.sleep(0, result="async-token")
                ),
                client=None,
            )
            client = await async_manager._get_client()
            self.assertIs(client, async_manager._client)
            self.assertEqual(await async_manager._get_token_result(), "async-token")
            self.assertEqual(
                async_manager._cache_token(
                    types.SimpleNamespace(token="token", expires_on=0), 100
                ),
                "token",
            )
            with self.assertRaises(SPAuthenticationError):
                async_manager._cache_token({}, 100)

            class SyncClosable:
                def __init__(self):
                    self.closed = False

                def close(self):
                    self.closed = True

            sync_response = SyncClosable()
            await AsyncSharepointManager._close_response(sync_response)
            self.assertTrue(sync_response.closed)

            for status, error in (
                (401, SPAuthorizationError),
                (404, SPNotFoundError),
                (409, SPConflictError),
                (429, SPThrottledError),
                (500, SPGraphError),
            ):
                response = Response(
                    status,
                    {"error": {"message": "sharing link no longer available"}},
                )
                with self.assertRaises(error):
                    async_manager._raise_for_status(response)

            async_manager.policy = OperationPolicy(
                max_total_bytes=1,
                max_file_bytes=1,
                max_disk_bytes=1,
                max_items=1,
                max_pages=1,
                max_depth=1,
            )
            budget = {"bytes": 0, "items": 0, "pages": 0, "started": 10**10}
            for kwargs in (
                {"byte_count": 2},
                {"items": 2},
                {"pages": 2},
                {"depth": 2},
            ):
                with self.assertRaises(SPValidationError):
                    async_manager._consume_budget(dict(budget), **kwargs)
            async_manager._check_chunk(b"", dict(budget))
            await async_manager.close()
            await async_manager.close()

        asyncio.run(exercise())

    def test_public_lifecycle_and_budget_edges(self):
        with (
            patch.object(SharepointManager, "_get_site_id", return_value="site"),
            patch.object(SharepointManager, "_get_drive_id", return_value="drive"),
            patch.object(
                SharepointManager, "_get_folder", return_value=SPFolder(id="root")
            ),
        ):
            initialized = SharepointManager(
                "https://tenant.sharepoint.com/sites/demo",
                token_provider=types.SimpleNamespace(get_token=lambda _scope: "token"),
                tenant_id="tenant-id",
                document_folder_name="Documents",
                session=types.SimpleNamespace(close=lambda: None),
            )
        self.assertEqual(initialized.site_name, "demo")
        self.assertEqual(initialized.relative_path_root, "/sites/demo/Documents")

        manager = object.__new__(SharepointManager)
        manager.policy = OperationPolicy(
            max_file_bytes=1,
            max_total_bytes=2,
            max_disk_bytes=2,
            max_items=1,
            max_pages=1,
        )
        manager._site_id = "site"
        manager._drive_id = "drive"
        manager._cached_token = "token"
        manager._cached_token_expiry = 100
        manager._password = "password"
        manager._account = object()
        manager.credentials = ClientCredential("id", "secret")
        manager._closed = False
        manager._active_requests = 0
        manager._request_condition = threading.Condition()
        manager._token_provider = types.SimpleNamespace(close=lambda: None)
        manager._owns_session = True

        class ClosingSession:
            def close(self):
                raise RuntimeError("already closed")

        manager._session = ClosingSession()
        manager._session_registry_lock = threading.Lock()
        manager._session_registry = {1: manager._session}
        manager.close()
        manager.close()
        self.assertTrue(manager._closed)
        self.assertTrue(manager.validate_resource_scope())
        with self.assertRaises(SPUnauthorizedTarget):
            manager.validate_resource_scope(site_id="other")
        with self.assertRaises(SPValidationError):
            manager._check_file_budget(2)
        with self.assertRaises(SPValidationError):
            manager._consume_operation_budget(
                {"bytes": 0, "items": 0, "pages": 0, "started": 10**10}, items=2
            )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "file.txt"
            path.write_text("x")
            manager._root_folder = SPFolder(id="root")
            manager._check_file_budget = lambda *_args, **_kwargs: None
            manager._upload_file_direct = lambda *_args: SPFile(
                id="file", name="file.txt"
            )
            self.assertEqual(manager.upload_file(str(path)).id, "file")


if __name__ == "__main__":
    unittest.main()
