from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet_key() -> bytes:
    configured = str(settings.integration_encryption_key or "").strip()
    if configured:
        return configured.encode("utf-8")
    digest = hashlib.sha256(str(settings.jwt_secret).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_fernet_key())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(str(value).encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(str(value).encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("invalid_encrypted_secret") from exc
