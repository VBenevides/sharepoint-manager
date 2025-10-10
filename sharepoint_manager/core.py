"""
Module used to interact with sharepoint sites using an approach similar to file systems
"""

# ---------------------------------------------------------------------- #
# Imports
# ---------------------------------------------------------------------- #

from typing import Any
from collections.abc import Iterator
import requests
import os
import re
import time

from automation_logging import info_else, error_else
from msal import ConfidentialClientApplication

from .decorators import retry_if_not_exception, retry
from .dataclasses import ClientCredential, SPFolder, SPFile
from .exceptions import SPFolderNotEmpty, SPFileNotFound, SPFolderNotFound
from .utils import get_filename, get_names_to_folder


class SharepointManager:
    """
    Provides an interface for interacting with a SharePoint site.


    Supports uploading, downloading, listing, and deleting files/folders
    using Microsoft Graph API.


    Examples
    --------
    >>> creds = ClientCredential("app_id", "app_secret")
    >>> manager = SharepointManager(
    ... sharepoint_site_url="https://my_tenant.sharepoint.com/sites/my_site",
    ... credentials=creds,
    ... )
    >>> manager.download_file(
    ... file="file.txt",
    ... local_download_path="./Download_Dir",
    ... sp_relative_folder_path="Folder/Subfolder"
    ... )
    >>> manager.upload_file(
    ... local_file_path="./Download_Dir/file.txt",
    ... sp_relative_folder_path="Folder/Subfolder2"
    ... )
    """

    def __init__(
        self,
        sharepoint_site_url: str,
        credentials: ClientCredential,
        document_folder_name: str = "Shared Documents",
    ) -> None:
        """
        Initializes the SharepointManager with a given SharePoint URL and credentials.

        Parameters
        ----------
        sharepoint_site_url : str
            The URL of the SharePoint site. E.g: 'https://{tenant_url}.sharepoint.com/sites/{site_name}'.
        credentials : ClientCredential
            Graph API credentials for authentication.
        document_folder_name : str, optional
            The name of the document folder in the SharePoint site. Default is "Shared Documents".

            This is vital to guarantee that the class will be able to find the documents in the site.

        Returns
        -------
        None

        Examples
        --------
        >>> user_cred = ClientCredential("graph_id", "graph_secret") # Don't hardcode passwords
        >>> manager = SharepointManager(sharepoint_site_url = "https://my_tenant.sharepoint.com/sites/my_site",
        >>>     credentials = user_cred,
        >>>     document_folder_name = "Shared Documents",
        >>> )
        """

        self._session: requests.Session = requests.Session()

        self.url: str = sharepoint_site_url
        self.tenant_url: str = sharepoint_site_url.split("/sites", maxsplit=1)[0]
        self.tenant_id: str = self._get_tenant_id()

        # These variables shouldn't be changed manually
        self.site_name: str = self.url.split("/sites/", maxsplit=1)[-1]
        self.cca: ConfidentialClientApplication = ConfidentialClientApplication(
            client_id=credentials.client_id,
            client_credential=credentials.client_secret,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
        )
        self.document_folder_name: str = document_folder_name
        self.relative_path_root: str = f"/sites/{self.site_name}/{document_folder_name}"
        self._site_id: str = self._get_site_id()
        self._drive_id: str = self._get_drive_id()

        self.folder: SPFolder = self._get_folder("")
        self.users: dict[str, Any] = {}

    # ----------------------------------------------------------
    # Support Methods
    # ----------------------------------------------------------

    def _get_site_id(self) -> str:
        parts = [x for x in self.url.split("/") if len(x) > 0]
        tenant = [x for x in parts if "share" in x.lower() or ".com" in x.lower()][0]
        site = self.url.split("/sites/")[-1]
        if "/sites/" not in site:
            site = f"/sites/{site}"
        url = f"https://graph.microsoft.com/v1.0/sites/{tenant}:{site}"
        r = self._request("GET", url, headers=self._hdr(), timeout=30)
        r.raise_for_status()
        self._site_id = r.json()["id"]
        return self._site_id

    def _hdr(self, json_content: bool = False) -> dict[str, Any]:
        token = self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _get_tenant_id(self) -> str:
        """Retrieve the tenant ID from the SharePoint tenant URL."""

        r = self._request("HEAD", self.tenant_url, headers={"Authorization": "Bearer"}, timeout=20)
        hdr = r.headers.get("WWW-Authenticate", "")
        m = re.search(r'realm="([^"]+)"', hdr)
        if m:
            return m.group(1)
        for item in hdr.split(","):
            if '="' in item:
                k, v = item.split("=", 1)
                v = v.strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                if k.strip().lower() == "bearer realm":
                    return str(v)

        raise RuntimeError("Cannot determine tenant id from WWW-Authenticate header")

    def _ensure_token(self) -> str:
        # msal already has an internal cache
        result = self.cca.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if not isinstance(result, dict) or "access_token" not in result.keys():
            error = result
            if isinstance(result, dict):
                error = result.get("error_description", result)
            raise RuntimeError(f"Authentication failed: {error}")
        return str(result["access_token"])

    def _get_drive_id(self) -> str:
        site_id = self._site_id
        r = self._request(
            "GET",
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
            headers=self._hdr(),
            timeout=30,
        )
        r.raise_for_status()
        for d in r.json().get("value", []):
            if d.get("name") == self.document_folder_name:
                self._drive_id = d["id"]
                return self._drive_id

        r = self._request(
            "GET",
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive",
            headers=self._hdr(),
            timeout=30,
        )
        if r.status_code == 200:
            self._drive_id = r.json()["id"]
            self.document_folder_name = r.json()["webUrl"].split("/")[-1]
            return self._drive_id
        raise RuntimeError("Drive not found for site")

    def _get_folder(self, folder_path: str) -> SPFolder:
        """folder_dict, folder_exists"""
        site_id = self._site_id
        drive_id = self._drive_id
        if folder_path != "":
            site = f":/{folder_path}"
        else:
            site = ""
        r = self._request(
            "GET",
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root{site}",
            headers=self._hdr(),
            timeout=30,
        )

        if r.status_code == 404:
            raise SPFolderNotFound(f"SP Folder not found: {folder_path}")
        r.raise_for_status()
        return SPFolder.from_dict(r.json())

    def _get_file(self, filename: str) -> SPFile:
        site_id = self._site_id
        drive_id = self._drive_id

        folder_id = str(self.folder.id)
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        for obj in self._paginate(url):
            if isinstance(obj, dict) and obj.get("name", "") == filename:
                return SPFile.from_dict(obj)
        raise SPFileNotFound("SP file not found")

    @retry(attempts=4, exceptions=Exception)
    def _create_folder(self, folder_path: str) -> SPFolder | None:
        try:
            return self._get_folder(folder_path)
        except Exception:
            pass

        drive_id = self._drive_id
        parts = folder_path.split("/")
        parent_folder = "/".join(parts[:-1])
        folder_name = parts[-1]
        try:
            parent_data = self._get_folder(parent_folder)
        except SPFolderNotFound:
            parent_data = self._create_folder(parent_folder)

        if parent_data is None:
            raise SPFolderNotFound("SP Parent folder not found")

        parent_id = parent_data.id

        payload = {"name": folder_name, "folder": {}}
        r = requests.post(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{parent_id}/children",
            headers=self._hdr(),
            timeout=30,
            json=payload,
        )
        r.raise_for_status()
        return SPFolder.from_dict(r.json())

    # ----------------------------------------------------------
    # Basic file system functions
    # ---------------------------------------------------------

    def get_file_author(self, file: SPFile) -> dict[str, dict[str, str]]:
        """
        Return author and editor metadata for a SharePoint file.


        Parameters
        ----------
        file : SPFile
            File object.


        Returns
        -------
        dict
            Dictionary with "author" and "editor" entries.
        """

        created_by = file.created_by
        author = {}
        user = list(created_by.keys())[0]
        created_by = created_by[user]
        author["id"] = created_by.get("id", "")
        author["display_name"] = created_by.get("displayName", "")
        author["email"] = created_by.get("email", "")

        modified_by = file.last_modified_by
        editor = {}
        user = list(modified_by.keys())[0]
        modified_by = modified_by[user]
        editor["id"] = modified_by.get("id", "")
        editor["display_name"] = modified_by.get("displayName", "")
        editor["email"] = modified_by.get("email", "")

        return {"author": author, "editor": editor}

    @retry_if_not_exception(attempts=3, exceptions=(SPFolderNotFound))
    def set_folder(self, sp_relative_folder_path: str, create_folder: bool = False) -> SPFolder:
        """
        Set the current working folder.


        Parameters
        ----------
        sp_relative_folder_path : str
            Relative path within the document library.
        create_folder : bool, optional
            If True, create the folder (and ancestors) if it does not exist.


        Returns
        -------
        SPFolder
            The set folder object.


        Raises
        ------
        SPFolderNotFound
            If the folder does not exist and `create_folder` is False.


        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> try:
        >>>     manager.set_folder(sp_relative_folder_path = "Folder1/Folder2/Folder3", create_folder = False)
        >>> except SPFolderNotFound:
        >>>     info_else("Folder does not exist inside Sharepoint!")
        >>> manager.set_folder(sp_relative_folder_path = "Folder1/Folder2/Folder3", create_folder = True) # Creates folder
        """

        # Change folder to the root folder (we always go from here to the target folder)
        self.folder = self._get_folder("")

        fnames = get_names_to_folder(sp_relative_folder_path)
        if len(fnames) == 0:
            return self.folder

        target_folder = "/".join(fnames)
        try:
            folder_data = self._get_folder(target_folder)
        except SPFolderNotFound:
            if create_folder:
                folder_data = self._create_folder(target_folder)
            else:
                raise SPFolderNotFound(f"SP Folder does not exist: {target_folder}")

        if folder_data is None or folder_data.name != fnames[-1]:
            self.folder = self._get_folder("")
            raise RuntimeError("SP Folder was not set correctly")
        self.folder = folder_data
        return self.folder

    def list_files(self, sp_relative_folder_path: str | None = None) -> dict[str, SPFile]:
        """
        List files in a SharePoint folder.


        Parameters
        ----------
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Returns
        -------
        dict
            Mapping of filename to SPFile objects.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> files = manager.list_files(sp_relative_folder_path = "Folder1/Folder2/Folder3") # Changes self.folder and lists the files
        """

        if sp_relative_folder_path is not None:
            self.folder = self.set_folder(sp_relative_folder_path)

        drive_id = self._drive_id
        folder_id = self.folder.id
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{folder_id}/children"
        files = {}
        for item in self._paginate(url):
            if "file" in item:
                _file = SPFile.from_dict(item)
                files[_file.name] = _file

        return files

    def list_folders(self, sp_relative_folder_path: str | None = None) -> dict[str, SPFolder]:
        """
        List subfolders in a SharePoint folder.


        Parameters
        ----------
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Returns
        -------
        dict
            Mapping of folder name to SPFolder objects.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> folders = manager.list_folders(sp_relative_folder_path = "Folder1/Folder2/Folder3") # Changes self.folder and lists the folders
        """

        if sp_relative_folder_path is not None:
            _ = self.set_folder(sp_relative_folder_path)

        drive_id = self._drive_id
        folder_id = self.folder.id
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{folder_id}/children"
        folders = {}
        for item in self._paginate(url):
            if "folder" in item:
                _folder = SPFolder.from_dict(item)
                folders[_folder.name] = _folder

        return folders

    # ----------------------------------------------------------
    # Upload files/folders to Sharepoint
    # ----------------------------------------------------------

    @retry_if_not_exception(attempts=3, exceptions=(FileNotFoundError))
    def upload_file(self, local_file_path: str, sp_relative_folder_path: str | None = None) -> None:
        """
        Upload a local file to SharePoint.


        Parameters
        ----------
        local_file_path : str
            Path to the local file.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Raises
        ------
        FileNotFoundError
            If the local file does not exist or is not a file.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.upload_file(local_file_path = "file.txt", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_file_path = os.path.abspath(local_file_path)
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(f"Local file does not exist: {local_file_path}")
        if not os.path.isfile(local_file_path):
            raise FileNotFoundError(f"Path does not correspond to a file: {local_file_path}")

        if sp_relative_folder_path is not None:
            _ = self.set_folder(sp_relative_folder_path, create_folder=True)

        file_name = get_filename(local_file_path)
        file_size_b = os.path.getsize(local_file_path)
        file_size_mb = file_size_b / (1024 * 1024)

        info_else(f"Uploading file {file_name} ({file_size_mb:.1f} MB)")

        with open(local_file_path, "rb") as file:
            site_id = self._site_id
            drive_id = self._drive_id
            folder_id = self.folder.id
            url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/items/{folder_id}:/{file_name}:/createUploadSession"
            request_body = {"@microsoft.graph.conflictBehavior": "replace"}
            r = self._request("POST", url, headers=self._hdr(), timeout=30, json=request_body)
            r.raise_for_status()
            upload_session = r.json()
            upload_url = str(upload_session["uploadUrl"])

            chunk_size = 20 * 327680  # 6.25 MiB
            start_byte = 0
            try:
                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break

                    end_byte = start_byte + len(chunk) - 1
                    content_range = f"bytes {start_byte}-{end_byte}/{file_size_b}"

                    chunk_headers = {
                        "Content-Length": str(len(chunk)),
                        "Content-Range": content_range,
                    }

                    for attempt in range(3):
                        try:
                            response = self._request(
                                "PUT",
                                upload_url,
                                headers=chunk_headers,
                                timeout=60,
                                data=chunk,
                            )
                            response.raise_for_status()
                            break
                        except Exception as e:
                            time.sleep(1)
                            if attempt >= 2:
                                error_else(f"Error uploading chunk: {e}")
                                raise e

                    start_byte += len(chunk)
                    info_else(
                        f"Uploaded {start_byte / (1024 * 1024):.1f} MiB out of {file_size_b / (1024 * 1024):.1f}"
                    )
            finally:
                try:
                    _ = self._request("DELETE", upload_url, timeout=30)
                except Exception:
                    pass

        info_else("Upload completed.")

    def upload_folder(
        self, local_folder_path: str, sp_relative_folder_path: str | None = None
    ) -> None:
        """
        Recursively upload a local folder and its contents to SharePoint.


        Parameters
        ----------
        local_folder_path : str
            Path to the local folder.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Raises
        ------
        FileNotFoundError
            If the local folder does not exist.
        ValueError
            If the path is not a folder.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.upload_folder(local_file_path = "./Folder4", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_folder_path = os.path.abspath(local_folder_path)
        if not os.path.exists(local_folder_path):
            raise FileNotFoundError(f"Local folder does not exist: {local_folder_path}")
        if not os.path.isdir(local_folder_path):
            raise ValueError(f"Path does not correspond to a folder: {local_folder_path}")

        if sp_relative_folder_path is not None:
            _ = self.set_folder(sp_relative_folder_path, create_folder=True)

        # Create folder inside of the current Sharepoint folder
        sp_relative_url = self.folder.relative_url
        new_folder_name = os.path.basename(local_folder_path)
        sp_folder_path = f"{sp_relative_url}/{new_folder_name}"
        if len(sp_folder_path) > 0 and sp_folder_path[0] == "/":
            sp_folder_path = sp_folder_path[1:]
        info_else(f"Uploading folder: {self.folder.name}")
        _ = self.set_folder(sp_folder_path, create_folder=True)

        list_elements = os.listdir(local_folder_path)
        # Upload files
        list_files = [
            f for f in list_elements if os.path.isfile(os.path.join(local_folder_path, f))
        ]
        for file_name in list_files:
            self.upload_file(os.path.join(local_folder_path, file_name))

        # Upload folders (recursive)
        list_folders = [
            f for f in list_elements if os.path.isdir(os.path.join(local_folder_path, f))
        ]
        for folder_name in list_folders:
            self.upload_folder(
                os.path.join(local_folder_path, folder_name),
                f"{sp_folder_path}",
            )

    # ----------------------------------------------------------
    # Download files/folders from Sharepoint
    # ----------------------------------------------------------

    @retry_if_not_exception(attempts=3, exceptions=(SPFileNotFound))
    def download_file(
        self,
        file: str | SPFile,
        local_download_path: str,
        sp_relative_folder_path: str | None = None,
        new_filename: str | None = None,
    ) -> SPFile:
        """
        Download a file from SharePoint.


        Parameters
        ----------
        file : str | SPFile
            Filename or SPFile instance.
        local_download_path : str
            Local folder to download into.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.
        new_filename : str, optional
            If provided, rename the downloaded file.


        Returns
        -------
        SPFile
            The downloaded file metadata.

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.download_file(filename = "file.txt", local_download_path = "./Download_Dir",
        ...     sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_download_path = os.path.abspath(local_download_path)

        os.makedirs(local_download_path, exist_ok=True)

        if isinstance(file, str):
            if sp_relative_folder_path is not None:
                _ = self.set_folder(sp_relative_folder_path)

            file_obj = self._get_file(file)
        else:
            file_obj = file

        file_size_bytes = int(file_obj.size)
        file_size_mbytes = round(file_size_bytes / (1024 * 1024), 1)
        download_url = file_obj.download_url
        info_else(f"Downloading file {file_obj.name} ({file_size_mbytes} MB)")

        chunk_size = 4 * 1024 * 1024
        downloaded_bytes = 0

        filename = file_obj.name if new_filename is None else new_filename
        with self._request("GET", download_url, stream=True, timeout=None) as r:
            r.raise_for_status()
            with open(f"{local_download_path}/{filename}", "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    _ = f.write(chunk)
                    downloaded_bytes += len(chunk)
                    info_else(
                        f"Downloaded {downloaded_bytes / (1024 * 1024):.1f} MiB out of {file_size_bytes / (1024 * 1024):.1f}"
                    )

        info_else("Download completed.")

        return file_obj

    @retry_if_not_exception(attempts=3, exceptions=(SPFolderNotFound, SPFileNotFound))
    def download_folder(
        self,
        local_download_path: str,
        sp_relative_folder_path: str | None = None,
    ) -> None:
        """
        Recursively download a SharePoint folder and its contents.


        Parameters
        ----------
        local_download_path : str
            Local destination path.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.

        Returns
        -------
        None

        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> # The code below will create a folder "Folder3" inside "./Download_Dir"
        >>> manager.download_folder(local_download_path = "./Download_Dir",
        ...     sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        local_download_path = os.path.abspath(local_download_path)

        if sp_relative_folder_path is not None:
            _ = self.set_folder(sp_relative_folder_path)

        # Create local folder
        info_else(f"Downloading folder: {self.folder.name}")
        cur_folder = self.folder
        cur_folder_download_path = os.path.join(local_download_path, cur_folder.name)
        os.makedirs(cur_folder_download_path, exist_ok=True)

        # Download Files
        list_files_names = self.list_files()
        for file in list_files_names.values():
            _ = self.download_file(file, cur_folder_download_path)

        # Download folders (recursive)
        list_folder_names = self.list_folders()

        for folder_name in list_folder_names:
            folder_srp = f"{cur_folder.relative_url}/{folder_name}"
            if len(folder_srp) > 0 and folder_srp[0] == "/":
                folder_srp = folder_srp[1:]
            self.download_folder(
                cur_folder_download_path,
                folder_srp,
            )

    def delete_file(self, file: str | SPFile, sp_relative_folder_path: str | None = None) -> None:
        """
        Delete a file from SharePoint.


        Parameters
        ----------
        file : str | SPFile
            Filename or SPFile instance.
        sp_relative_folder_path : str, optional
            Relative path within the document library. If omitted, uses the current folder.


        Returns
        -------
        None


        Raises
        ------
        SPFileNotFound
            If the file does not exist.


        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> manager.delete_file(filename = "file.txt", sp_relative_folder_path = "Folder1/Folder2/Folder3")
        """

        if sp_relative_folder_path is not None:
            _ = self.set_folder(sp_relative_folder_path)

        if isinstance(file, str):
            file = self._get_file(file)

        drive_id = self._drive_id
        item_id = file.id
        r = requests.delete(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}",
            headers=self._hdr(),
            timeout=30,
        )
        r.raise_for_status()

    def delete_folder(self, folder: str | SPFolder, force_delete: bool = False) -> None:
        """
        Delete a SharePoint folder.


        Parameters
        ----------
        folder : str | SPFolder
            Relative path or folder object.
        force_delete : bool, optional
            If False (default), only empty folders are deleted. If True, delete regardless.


        Raises
        ------
        SPFolderNotEmpty
            If the folder is not empty and `force_delete` is False.


        Returns
        -------
        None


        Examples
        --------
        >>> manager = SharepointManager(...)
        >>> # Consider that the folder is not empty
        >>> try:
        >>>     manager.delete_folder(sp_relative_folder_path = "Folder1/Folder2/Folder3", force_delete=False)
        >>> except SPFolderNotEmpty:
        >>>     info_else("Sharepoint folder is not empty")
        >>> manager.delete_folder(sp_relative_folder_path = "Folder1/Folder2/Folder3", force_delete=True)
        """

        if isinstance(folder, str):
            folder = self.set_folder(folder)

        files = self.list_files()
        folders = self.list_folders()

        if (len(files) == 0 and len(folders) == 0) or force_delete:
            drive_id = self._drive_id
            folder_id = folder.id
            r = self._request(
                "DELETE",
                f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{folder_id}",
                headers=self._hdr(),
                timeout=30,
            )
            r.raise_for_status()
        else:
            raise SPFolderNotEmpty("Sharepoint folder not empty")

    # ----------------------------------------------------------
    # Internal HTTP helpers
    # ----------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int | None = 30,
        json: Any | None = None,
        data: Any | None = None,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        max_attempts: int = 5,
    ) -> requests.Response:
        attempt = 1
        while True:
            resp = self._session.request(
                method=method,
                url=url,
                headers=headers,
                timeout=timeout,
                json=json,
                data=data,
                params=params,
                stream=stream,
            )
            # Handle 429/503 with Retry-After
            if resp.status_code in (429, 503) and attempt < max_attempts:
                retry_after = resp.headers.get("Retry-After")
                delay = None
                try:
                    delay = int(retry_after) if retry_after is not None else None
                except Exception:
                    delay = None
                if delay is None:
                    delay = min(2**attempt, 60)
                time.sleep(delay)
                attempt += 1
                continue
            return resp

    def _paginate(self, url: str) -> Iterator[dict[str, Any]]:
        """Yield items across Graph pages following @odata.nextLink."""
        next_url = url
        while next_url:
            r = self._request("GET", next_url, headers=self._hdr(), timeout=30)
            r.raise_for_status()
            data = r.json()
            for item in data.get("value", []):
                yield item
            next_url = data.get("@odata.nextLink")
