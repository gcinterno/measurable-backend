from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, expires_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "token_use": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def create_report_export_token(
    subject: str,
    *,
    report_id: int,
    version: int,
    expires_seconds: int = 300,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "token_use": "report_export",
        "report_id": int(report_id),
        "version": int(version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])


class TokenError(Exception):
    pass


def get_subject(token: str) -> str:
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise TokenError("invalid_token") from exc
    subject = payload.get("sub")
    if not subject:
        raise TokenError("missing_subject")
    return str(subject)


def get_token_payload(token: str) -> dict:
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise TokenError("invalid_token") from exc
    if not payload.get("sub"):
        raise TokenError("missing_subject")
    return payload
