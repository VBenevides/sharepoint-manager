import os
import tempfile
from pathlib import Path

from sharepoint_manager import ClientCredential, SharepointManager
from sharepoint_manager.exceptions import SPAuthorizationError


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing staging configuration: {name}")
    return value


def main() -> None:
    site_url = required("SP_STAGING_SITE_URL")
    client_id = required("SP_STAGING_CLIENT_ID")
    client_secret = required("SP_STAGING_CLIENT_SECRET")
    drive_name = required("SP_STAGING_DRIVE_NAME")
    folder_path = required("SP_STAGING_FOLDER")
    credentials = ClientCredential(client_id, client_secret)
    manager = SharepointManager(site_url, credentials, drive_name)
    try:
        manager.list_files(folder_path)
        manager.list_folders(folder_path)
        manager.get_folder_delta(folder_path)
        if os.environ.get("SP_STAGING_ALLOW_DESTRUCTIVE") == "true":
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory, "staging-smoke-empty.txt")
                path.touch()
                item = manager.upload_file(
                    str(path), folder_path, conflict_behavior="fail"
                )
                manager.delete_file(item)
    finally:
        manager.close()

    denied_site = os.environ.get("SP_STAGING_DENIED_SITE_URL", "").strip()
    if denied_site:
        try:
            denied = SharepointManager(denied_site, credentials, drive_name)
            try:
                denied.list_files()
            finally:
                denied.close()
        except SPAuthorizationError:
            pass
        else:
            raise AssertionError("staging identity was not denied on the control site")
    print("staging smoke passed")


if __name__ == "__main__":
    main()
