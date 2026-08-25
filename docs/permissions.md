# Deployment permissions

Use Microsoft Graph `Sites.Selected` for this library. Grant the application
only the configured SharePoint site, with `read` or `write` access matching the
operation set. Use separate read and write identities when the deployment does
not need both capabilities.

## Caller boundary

This package serves trusted automation. It authenticates the configured
deployment identity and enforces its site/drive boundary; it does not know who
called a method and must not be exposed directly as a multi-user authorization
endpoint.

For an end-user product, place an authenticated service or proxy in front of
the package. That boundary must fail closed, evaluate direct, Entra-group, and
SharePoint-group grants for the current principal, and keep authorization
decisions isolated per request. Never reuse an ACL decision across principals.

Use the narrowest Selected permission that matches the resource scope:
`Sites.Selected` for sites, `Lists.SelectedOperations.Selected` for lists,
`ListItems.SelectedOperations.Selected` for items, or
`Files.SelectedOperations.Selected` for files. Consent, resource assignment,
and the corresponding token scope are all required.

At startup, compare the granted site and drive identifiers with the manager:

```python
manager.validate_resource_scope(site_id=expected_site_id, drive_id=expected_drive_id)
```

The library does not grant permissions or make end-user authorization
decisions. The deployment identity and its resource grant remain operator
owned. Do not use tenant-wide `Sites.ReadWrite.All` when selected-resource
access is sufficient.
