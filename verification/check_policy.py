import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
msal = types.ModuleType("msal")
msal.ConfidentialClientApplication = type("Confidential", (), {})
msal.PublicClientApplication = type("Public", (), {})
sys.modules.setdefault("msal", msal)

from sharepoint_manager.dataclasses import OperationPolicy  # noqa: E402


def main() -> None:
    policy = OperationPolicy(allow_capability_redirects=False, redact_logs=True)
    assert not policy.allow_capability_redirects and policy.redact_logs
    for kwargs in ({"allow_capability_redirects": 1}, {"redact_logs": "yes"}):
        try:
            OperationPolicy(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(kwargs)


if __name__ == "__main__":
    main()
