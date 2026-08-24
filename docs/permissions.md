# Deployment permissions

Use Microsoft Graph `Sites.Selected` for this library. Grant the application
only the configured SharePoint site, with `read` or `write` access matching the
operation set. Use separate read and write identities when the deployment does
not need both capabilities.

At startup, compare the granted site and drive identifiers with the manager:

```python
manager.validate_resource_scope(site_id=expected_site_id, drive_id=expected_drive_id)
```

The library does not grant permissions or make end-user authorization
decisions. The deployment identity and its resource grant remain operator
owned. Do not use tenant-wide `Sites.ReadWrite.All` when selected-resource
access is sufficient.
