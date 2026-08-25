import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.core import SharepointManager
from sharepoint_manager import core
from sharepoint_manager.dataclasses import OperationPolicy
from sharepoint_manager.exceptions import SPValidationError


class Response:
    status_code = 200
    headers = {}

    def close(self):
        return None


class Session:
    def __init__(self, state):
        self.state = state

    def request(self, **kwargs):
        with self.state.lock:
            self.state.active += 1
            self.state.maximum = max(self.state.maximum, self.state.active)
        try:
            self.state.started.wait(timeout=2)
            self.state.release.wait(timeout=2)
            return Response()
        finally:
            with self.state.lock:
                self.state.active -= 1

    def close(self):
        return None


class State:
    def __init__(self, workers):
        self.started = threading.Barrier(workers)
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0


def main() -> None:
    workers = 3
    state = State(workers)
    session = Session(state)
    original_session = core.requests.Session
    core.requests.Session = lambda: Session(state)
    manager = object.__new__(SharepointManager)
    manager.graph_host = "graph.microsoft.com"
    manager.tenant_url = "https://tenant.sharepoint.com"
    manager.policy = OperationPolicy(max_concurrency=workers)
    manager._session = session
    manager._owns_session = True
    manager._closed = False
    manager._request_gate = threading.BoundedSemaphore(workers)
    manager._request_condition = threading.Condition()
    manager._active_requests = 0
    manager._owner_thread_id = threading.get_ident()
    manager._session_local = threading.local()
    manager._session_local.session = session
    manager._session_registry_lock = threading.Lock()
    manager._session_registry = {manager._owner_thread_id: session}
    manager._shared_session_lock = threading.Lock()
    errors = []

    def request() -> None:
        try:
            manager._request(
                "GET",
                "https://graph.microsoft.com/v1.0/items",
                headers={"Authorization": "Bearer token"},
            ).close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=request) for _ in range(workers)]
    for thread in threads:
        thread.start()
    time.sleep(0.05)
    assert state.maximum > 1
    assert state.maximum <= workers
    state.release.set()
    for thread in threads:
        thread.join(timeout=2)
    assert not errors

    state = State(1)
    session = Session(state)
    core.requests.Session = lambda: Session(state)
    manager._session = session
    manager._closed = False
    manager._active_requests = 0
    manager._request_gate = threading.BoundedSemaphore(1)
    manager._request_condition = threading.Condition()
    manager._owner_thread_id = threading.get_ident()
    manager._session_local = threading.local()
    manager._session_local.session = session
    manager._session_registry_lock = threading.Lock()
    manager._session_registry = {manager._owner_thread_id: session}
    manager._shared_session_lock = threading.Lock()
    thread = threading.Thread(target=request)
    thread.start()
    time.sleep(0.05)
    closer = threading.Thread(target=manager.close)
    closer.start()
    time.sleep(0.05)
    assert closer.is_alive()
    state.release.set()
    thread.join(timeout=2)
    closer.join(timeout=2)
    assert not errors
    try:
        manager._request(
            "GET",
            "https://graph.microsoft.com/v1.0/items",
            headers={"Authorization": "Bearer token"},
        )
    except SPValidationError:
        pass
    else:
        raise AssertionError("request accepted after close")
    core.requests.Session = original_session


if __name__ == "__main__":
    main()
