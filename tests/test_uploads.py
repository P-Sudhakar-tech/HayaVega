import tempfile
import unittest
from pathlib import Path

from hayavega.request import UploadedFile
from hayavega.uploads import save_uploaded_file


class SaveUploadedFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def test_writes_the_files_content_under_its_own_filename(self):
        upload = UploadedFile(field_name="avatar", filename="pic.png", content_type="image/png", content=b"\x89PNG-fake-bytes")
        destination = save_uploaded_file(upload, self.directory.name)
        self.assertEqual(destination, Path(self.directory.name) / "pic.png")
        self.assertEqual(destination.read_bytes(), b"\x89PNG-fake-bytes")

    def test_creates_the_destination_directory_if_missing(self):
        upload = UploadedFile(field_name="avatar", filename="pic.png", content_type="image/png", content=b"data")
        destination = save_uploaded_file(upload, Path(self.directory.name) / "uploads" / "avatars")
        self.assertTrue(destination.is_file())

    def test_strips_directory_traversal_from_the_filename(self):
        upload = UploadedFile(field_name="avatar", filename="../../etc/passwd", content_type="text/plain", content=b"data")
        destination = save_uploaded_file(upload, self.directory.name)
        self.assertEqual(destination, Path(self.directory.name) / "passwd")

    def test_falls_back_to_a_default_name_when_filename_is_empty(self):
        upload = UploadedFile(field_name="avatar", filename="", content_type=None, content=b"data")
        destination = save_uploaded_file(upload, self.directory.name)
        self.assertEqual(destination.name, "upload")


if __name__ == "__main__":
    unittest.main()
