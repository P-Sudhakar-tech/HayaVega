import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mangoo.mail import ConsoleEmailBackend, EmailMessage, FileEmailBackend, SMTPEmailBackend, backend_from_settings, mail_notifier, send_mail


class ConsoleEmailBackendTests(unittest.TestCase):
    def test_logs_the_message(self):
        with self.assertLogs("mangoo.mail", level="INFO") as captured:
            send_mail(EmailMessage(to=("alice@example.com",), subject="Hi", body="Hello there"), ConsoleEmailBackend())
        self.assertIn("Hi", captured.output[0])
        self.assertIn("alice@example.com", captured.output[0])


class FileEmailBackendTests(unittest.TestCase):
    def test_writes_one_file_per_message(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FileEmailBackend(directory)
            send_mail(EmailMessage(to=("alice@example.com",), subject="Hi", body="Hello there"), backend)
            files = list(Path(directory).glob("*.eml"))
            self.assertEqual(len(files), 1)
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("Hello there", content)
            self.assertIn("alice@example.com", content)

    def test_creates_the_directory_if_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FileEmailBackend(Path(directory) / "outbox")
            send_mail(EmailMessage(to=("alice@example.com",), subject="Hi", body="Hello"), backend)
            self.assertTrue((Path(directory) / "outbox").is_dir())


class SmtpEmailBackendTests(unittest.TestCase):
    @patch("mangoo.mail.smtplib.SMTP")
    def test_sends_over_smtp_with_starttls_and_login(self, smtp_class):
        connection = MagicMock()
        smtp_class.return_value.__enter__.return_value = connection
        backend = SMTPEmailBackend("smtp.example.com", port=587, username="user", password="pw")
        send_mail(EmailMessage(to=("alice@example.com",), subject="Hi", body="Hello"), backend)
        smtp_class.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
        connection.starttls.assert_called_once()
        connection.login.assert_called_once_with("user", "pw")
        connection.send_message.assert_called_once()

    @patch("mangoo.mail.smtplib.SMTP")
    def test_skips_starttls_and_login_when_not_configured(self, smtp_class):
        connection = MagicMock()
        smtp_class.return_value.__enter__.return_value = connection
        backend = SMTPEmailBackend("smtp.example.com", use_tls=False)
        send_mail(EmailMessage(to=("alice@example.com",), subject="Hi", body="Hello"), backend)
        connection.starttls.assert_not_called()
        connection.login.assert_not_called()


class BackendFromSettingsTests(unittest.TestCase):
    def test_defaults_to_console(self):
        self.assertIsInstance(backend_from_settings({}), ConsoleEmailBackend)

    def test_builds_a_file_backend(self):
        backend = backend_from_settings({"BACKEND": "file", "DIRECTORY": "/tmp/outbox"})
        self.assertIsInstance(backend, FileEmailBackend)
        self.assertEqual(backend.directory, Path("/tmp/outbox"))

    def test_file_backend_requires_a_directory(self):
        with self.assertRaisesRegex(ValueError, "directory"):
            backend_from_settings({"BACKEND": "file"})

    def test_builds_an_smtp_backend(self):
        backend = backend_from_settings({"BACKEND": "smtp", "HOST": "smtp.example.com", "PORT": 2525, "USERNAME": "user", "USE_TLS": False})
        self.assertIsInstance(backend, SMTPEmailBackend)
        self.assertEqual(backend.host, "smtp.example.com")
        self.assertEqual(backend.port, 2525)
        self.assertFalse(backend.use_tls)

    def test_smtp_backend_requires_a_host(self):
        with self.assertRaisesRegex(ValueError, "host"):
            backend_from_settings({"BACKEND": "smtp"})

    def test_rejects_an_unknown_backend(self):
        with self.assertRaisesRegex(ValueError, "console.*file.*smtp"):
            backend_from_settings({"BACKEND": "carrier-pigeon"})


class MailNotifierTests(unittest.TestCase):
    def test_adapts_a_backend_into_the_notify_callable_contract(self):
        from types import SimpleNamespace

        sent = []
        backend = SimpleNamespace(send=lambda message: sent.append(message))
        notify = mail_notifier(backend)
        user = SimpleNamespace(email="alice@example.com")
        notify("password_reset", user, "abc123")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].to, ("alice@example.com",))
        self.assertIn("abc123", sent[0].body)
        self.assertEqual(sent[0].subject, "Reset your password")


if __name__ == "__main__":
    unittest.main()
