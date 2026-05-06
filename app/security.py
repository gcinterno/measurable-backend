from datetime import datetime, timedelta, timezone
import secrets

from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.hash import pbkdf2_sha256

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="pbkdf2_sha256")


def hash_password(password: str) -> str:
    try:
        return pwd_context.hash(password)
    except Exception:
        return pbkdf2_sha256.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_verification_code(code: str) -> str:
    try:
        return pwd_context.hash(code)
    except Exception:
        return pbkdf2_sha256.hash(code)


def verify_verification_code(code: str, code_hash: str) -> bool:
    return pwd_context.verify(code, code_hash)


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


def create_oauth_state(*, purpose: str, expires_seconds: int = 600) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": purpose,
        "nonce": secrets.token_urlsafe(32),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as exc:
        raise TokenError("invalid_state") from exc
    if not payload.get("purpose"):
        raise TokenError("missing_purpose")
    return payload


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
