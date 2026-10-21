# File uploads

Parses a `multipart/form-data` request body into plain fields and
uploaded files.

## Requirements

Needs `python-multipart` (`uv add python-multipart`) -- kept optional,
the same policy as `cryptography` for TOTP encryption: not a mandatory
dependency for every project, including ones that never accept file
uploads. Without it, `request.files()` raises `MultipartUnavailableError`
with the install hint; `request.form()` still works for plain
`application/x-www-form-urlencoded` bodies regardless.

## Reading an upload

```python
from mangoo import api_route
from mangoo.uploads import save_uploaded_file


def upload_avatar(request):
    files = request.files()
    avatar = files.get("avatar")
    if avatar is None:
        return {"detail": "avatar is required"}, 400
    path = save_uploaded_file(avatar, "uploads/avatars")
    return {"filename": avatar.filename, "content_type": avatar.content_type, "size": avatar.size, "saved_to": str(path)}
```

`request.files()` returns `{field_name: UploadedFile}` -- the first
file, if more than one was submitted under the same field name.
`request.form()` on the same multipart request returns the non-file
fields, exactly like it already does for urlencoded forms:

```python
request.form()   # {"title": "My photo"}
request.files()  # {"avatar": UploadedFile(...)}
```

## `UploadedFile`

| Attribute | Type | |
|---|---|---|
| `field_name` | `str` | The form field name |
| `filename` | `str` | Client-supplied -- see the storage note below |
| `content_type` | `str \| None` | |
| `content` | `bytes` | The whole file, in memory |
| `size` | `int` | `len(content)` |

Held entirely in memory, bounded by the existing
`HardeningOptions.max_request_bytes` check (already runs before the
body is read at all) -- there is no streaming-to-disk path for very
large uploads in this version.

## Saving an upload

`save_uploaded_file(file, directory)` writes `file.content` to
`directory` under `file.filename` and returns the resulting `Path`.
This is a plain local-disk helper, not an abstracted storage-backend
system (S3 and similar remain out of scope). `file.filename` is
client-supplied: only its basename is used (directory-traversal
segments are stripped), but a production use that needs a safe,
unique, or collision-free name should generate one itself rather than
trust the client's filename directly.
