"""Encryption for users' own API keys (Fernet / AES-128-CBC + HMAC)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class EncryptionUnavailable(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().app_encryption_key
    if not key:
        raise EncryptionUnavailable("APP_ENCRYPTION_KEY is not configured on the server")
    return Fernet(key.encode())


def encryption_available() -> bool:
    try:
        _fernet()
        return True
    except (EncryptionUnavailable, ValueError):
        return False


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, EncryptionUnavailable, ValueError):
        return None


def key_hint(key: str) -> str:
    return f"…{key[-4:]}" if len(key) >= 8 else "…"
