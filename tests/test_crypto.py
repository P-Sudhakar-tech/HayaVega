import sys
import unittest

from hayavega import crypto


class EncryptDecryptTests(unittest.TestCase):
    def test_round_trips_a_plaintext_value(self):
        encoded = crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        self.assertEqual(crypto.decrypt("a-real-unique-secret", encoded), "JBSWY3DPEHPK3PXP")

    def test_encrypted_values_are_recognized_and_not_the_plaintext(self):
        encoded = crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        self.assertTrue(crypto.is_encrypted(encoded))
        self.assertNotIn("JBSWY3DPEHPK3PXP", encoded)

    def test_plain_values_are_not_recognized_as_encrypted(self):
        self.assertFalse(crypto.is_encrypted("JBSWY3DPEHPK3PXP"))

    def test_two_encryptions_of_the_same_plaintext_differ(self):
        """A fresh random nonce each time -- otherwise identical TOTP
        secrets would produce identical ciphertext, leaking equality."""
        first = crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        second = crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        self.assertNotEqual(first, second)

    def test_decrypt_rejects_the_wrong_secret_key(self):
        encoded = crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        with self.assertRaises(ValueError):
            crypto.decrypt("a-different-secret", encoded)

    def test_decrypt_rejects_tampered_ciphertext(self):
        encoded = crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        format_, version, nonce, ciphertext = encoded.split("$")
        tampered = f"{format_}${version}${nonce}${ciphertext[:-4]}AAAA"
        with self.assertRaises(ValueError):
            crypto.decrypt("a-real-unique-secret", tampered)

    def test_decrypt_rejects_an_unrecognized_format(self):
        with self.assertRaises(ValueError):
            crypto.decrypt("a-real-unique-secret", "not-an-encrypted-value")

    def test_available_reflects_whether_cryptography_is_installed(self):
        self.assertIsInstance(crypto.available(), bool)


class CryptographyUnavailableTests(unittest.TestCase):
    """`available()`, `encrypt()`, and `decrypt()` each import `cryptography`
    (or one of its submodules) lazily, inside the function body, and treat
    an `ImportError` as "not installed" -- forced here via `sys.modules`,
    since `cryptography` itself is installed in this environment. Every
    already-imported `cryptography`/`cryptography.*` submodule is removed
    first so a cached submodule can't bypass the forced failure, and
    everything is restored afterwards so later tests see a normal,
    available `cryptography`."""

    def _force_cryptography_missing(self) -> dict:
        removed = {name: sys.modules.pop(name) for name in list(sys.modules) if name == "cryptography" or name.startswith("cryptography.")}
        sys.modules["cryptography"] = None  # sentinel: forces ImportError on import
        return removed

    def _restore_cryptography(self, removed: dict) -> None:
        sys.modules.pop("cryptography", None)
        sys.modules.update(removed)

    def test_available_returns_false_when_cryptography_is_not_installed(self):
        removed = self._force_cryptography_missing()
        try:
            self.assertFalse(crypto.available())
        finally:
            self._restore_cryptography(removed)

    def test_encrypt_raises_encryption_unavailable_when_cryptography_is_not_installed(self):
        removed = self._force_cryptography_missing()
        try:
            with self.assertRaises(crypto.EncryptionUnavailableError):
                crypto.encrypt("a-real-unique-secret", "JBSWY3DPEHPK3PXP")
        finally:
            self._restore_cryptography(removed)

    def test_decrypt_raises_encryption_unavailable_when_cryptography_is_not_installed(self):
        removed = self._force_cryptography_missing()
        try:
            with self.assertRaises(crypto.EncryptionUnavailableError):
                crypto.decrypt("a-real-unique-secret", "aesgcm$1$abc$def")
        finally:
            self._restore_cryptography(removed)
