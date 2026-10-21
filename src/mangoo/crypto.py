"""Optional, project-`secret_key`-derived encryption at rest.

This is the one place `Settings.secret_key` is actually load-bearing --
everywhere else in Mangoo (session tokens, password-reset/email-verification
tokens, TOTP secret generation) uses independent randomness from `secrets`,
never a key derived from `secret_key`. `enable_totp` uses this module to
encrypt the TOTP secret before it's stored, so a stolen database alone
doesn't hand over every user's live two-factor secret.

Requires `cryptography` (`uv add cryptography`), kept optional -- same
policy as the `psycopg`/`pymysql` database drivers -- rather than a new
mandatory dependency for every project, including ones that never enable
TOTP. Without it, callers fall back to storing the plaintext value; `mango
diagnose` reports that fallback so it's never a silent downgrade (see
`checks.py`).
"""

from __future__ import annotations

import base64
import os

_FORMAT = "aesgcm"
_VERSION = "1"
_INFO = b"mangoo.crypto.totp_secret.v1"


class EncryptionUnavailableError(RuntimeError):
    """Raised when `cryptography` isn't installed."""


def available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def _derive_key(secret_key: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(secret_key.encode("utf-8"))


def is_encrypted(value: str) -> bool:
    """True if `value` looks like something `encrypt` produced, not a raw secret."""
    return value.startswith(f"{_FORMAT}$")


def encrypt(secret_key: str, plaintext: str) -> str:
    """Encrypt `plaintext` with a key derived from `secret_key` via HKDF-SHA256, AES-256-GCM.

    Returns ``"aesgcm$1$<base64 nonce>$<base64 ciphertext+tag>"`` -- the
    same ``$``-delimited, versioned format `auth.hash_password` already
    uses for its own stored values. Raises `EncryptionUnavailableError` if
    `cryptography` isn't installed; callers decide whether to fall back to
    plaintext or propagate it.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise EncryptionUnavailableError("Encrypting this value requires `cryptography` -- `uv add cryptography`.") from error
    key = _derive_key(secret_key)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return f"{_FORMAT}${_VERSION}${base64.b64encode(nonce).decode('ascii')}${base64.b64encode(ciphertext).decode('ascii')}"


def decrypt(secret_key: str, encoded: str) -> str:
    """Decrypt a value `encrypt` produced.

    Raises `ValueError` if `encoded` isn't in the expected format, the
    `secret_key` is wrong, or the value was tampered with (AES-GCM's
    authentication tag fails to verify) -- all three collapse to the same
    `ValueError` since there's nothing more specific a caller can safely
    do about any of them. Raises `EncryptionUnavailableError` if
    `cryptography` isn't installed.
    """
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise EncryptionUnavailableError("Decrypting this value requires `cryptography` -- `uv add cryptography`.") from error
    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != _FORMAT or parts[1] != _VERSION:
        raise ValueError(f"Not a recognized encrypted value: {encoded!r}")
    key = _derive_key(secret_key)
    try:
        nonce = base64.b64decode(parts[2])
        ciphertext = base64.b64decode(parts[3])
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError) as error:
        raise ValueError("Could not decrypt value -- wrong secret_key, or the value was tampered with.") from error
    return plaintext.decode("utf-8")
