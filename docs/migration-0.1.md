# 0.1.x migration note

Version 0.1.x is a breaking release. Update callers to the supported contract;
the package does not provide compatibility aliases or a deprecation window.

- Replace `set_folder()` and `cwd()` with an explicit path, URL, or resolved
  `SPFolder` passed to each operation.
- Replace `get_folder_delta()` and `get_folder_delta_from_url()` with
  `iter_folder_delta()`. Persist each emitted checkpoint and resume with its
  `delta_link`; do not materialize an unbounded delta result in the client.
- Use `upload_file_to_folder_url()`, `upload_folder_to_folder_url()`, and
  `download_folder_from_url()` for URL-targeted transfers.
- Use `ClientCredential` for app registration and
  `UserDelegatedCredential` only for the explicitly warned service-account
  username/password flow. The latter uses the password for bootstrap, then
  relies on the in-memory MSAL cache and silent acquisition.
- Replace broad exception handling with the exported `SP*` exception classes
  and pass an explicit `OperationPolicy` for transfer limits.
