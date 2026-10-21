import unittest
from unittest.mock import patch

from mangoo.request import MultipartUnavailableError, Request


def _multipart_body(boundary: str, *, title: str, filename: str, file_content: bytes, file_content_type: str = "image/png") -> bytes:
    field_part = f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\n{title}\r\n'.encode()
    file_header = f'--{boundary}\r\nContent-Disposition: form-data; name="avatar"; filename="{filename}"\r\nContent-Type: {file_content_type}\r\n\r\n'.encode()
    closing = f"--{boundary}--\r\n".encode()
    return field_part + file_header + file_content + b"\r\n" + closing


def _request(body: bytes, boundary: str) -> Request:
    return Request(method="POST", path="/upload", query_params={}, body=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


class MultipartParsingTests(unittest.TestCase):
    def setUp(self):
        self.boundary = "MangooTestBoundary123"
        self.body = _multipart_body(self.boundary, title="Hello World", filename="pic.png", file_content=b"\x89PNG-fake-bytes")

    def test_form_returns_plain_fields(self):
        request = _request(self.body, self.boundary)
        self.assertEqual(request.form(), {"title": "Hello World"})

    def test_files_returns_the_uploaded_file(self):
        request = _request(self.body, self.boundary)
        files = request.files()
        self.assertEqual(set(files), {"avatar"})
        uploaded = files["avatar"]
        self.assertEqual(uploaded.filename, "pic.png")
        self.assertEqual(uploaded.content_type, "image/png")
        self.assertEqual(uploaded.content, b"\x89PNG-fake-bytes")
        self.assertEqual(uploaded.size, len(b"\x89PNG-fake-bytes"))

    def test_files_is_empty_for_a_non_multipart_request(self):
        request = Request(method="POST", path="/", query_params={}, body=b"a=1", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(request.files(), {})

    def test_form_still_parses_urlencoded_bodies_unchanged(self):
        request = Request(method="POST", path="/", query_params={}, body=b"a=1&b=2", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(request.form(), {"a": "1", "b": "2"})

    def test_form_still_parses_urlencoded_bodies_with_no_content_type_header(self):
        """Pre-existing behavior: `form()` never required a Content-Type
        header before multipart support was added -- must still work."""
        request = Request(method="POST", path="/", query_params={}, body=b"a=1", headers={})
        self.assertEqual(request.form(), {"a": "1"})

    @patch.dict("sys.modules", {"python_multipart": None})
    def test_raises_a_clear_error_when_python_multipart_is_not_installed(self):
        request = _request(self.body, self.boundary)
        with self.assertRaisesRegex(MultipartUnavailableError, "python-multipart"):
            request.files()


if __name__ == "__main__":
    unittest.main()
