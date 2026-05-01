from collections.abc import Generator
from functools import lru_cache

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, load_only

from .db import SessionLocal, engine
from .errors import http_error
from .models import User
from .security import TokenError, get_subject, get_token_payload


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


auth_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def user_logo_column_available() -> bool:
    try:
        columns = inspect(engine).get_columns("users")
    except SQLAlchemyError:
        return False
    return any(str(column.get("name")) == "logo_url" for column in columns)


def load_current_user(db: Session, user_id: int) -> User | None:
    if user_logo_column_available():
        return db.get(User, user_id)
    return (
        db.query(User)
        .options(
            load_only(
                User.id,
                User.email,
                User.full_name,
                User.is_active,
                User.created_at,
                User.updated_at,
            )
        )
        .filter(User.id == user_id)
        .first()
    )


def load_user_by_email(db: Session, email: str) -> User | None:
    query = db.query(User).filter(User.email == email)
    if user_logo_column_available():
        return query.first()
    return query.options(
        load_only(
            User.id,
            User.email,
            User.password_hash,
            User.full_name,
            User.is_active,
            User.created_at,
            User.updated_at,
        )
    ).first()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise http_error(401, "missing_token", "Authorization token required.")
    try:
        subject = get_subject(credentials.credentials)
    except TokenError:
        raise http_error(401, "invalid_token", "Invalid or expired token.")
    user = load_current_user(db, int(subject))
    if not user or not user.is_active:
        raise http_error(401, "invalid_user", "User not found or inactive.")
    return user


def get_current_user_for_report_read(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise http_error(401, "missing_token", "Authorization token required.")
    try:
        payload = get_token_payload(credentials.credentials)
    except TokenError:
        raise http_error(401, "invalid_token", "Invalid or expired token.")

    subject = str(payload.get("sub"))
    token_use = str(payload.get("token_use") or "access")
    user = load_current_user(db, int(subject))
    if not user or not user.is_active:
        raise http_error(401, "invalid_user", "User not found or inactive.")

    if token_use == "access":
        return user

    if token_use != "report_export":
        raise http_error(401, "invalid_token", "Invalid token type.")

    requested_report_id = request.path_params.get("report_id")
    requested_version = request.path_params.get("version")
    scoped_report_id = payload.get("report_id")
    scoped_version = payload.get("version")
    if requested_report_id is not None and str(scoped_report_id) != str(requested_report_id):
        raise http_error(403, "invalid_export_scope", "Export token scope does not match report.")
    if requested_version is not None and scoped_version is not None and str(scoped_version) != str(
        requested_version
    ):
        raise http_error(403, "invalid_export_scope", "Export token scope does not match version.")
    return user
