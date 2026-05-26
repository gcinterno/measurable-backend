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


@lru_cache(maxsize=1)
def user_onboarding_columns_available() -> bool:
    required = {"onboarding_completed", "user_type", "goals", "platforms"}
    try:
        columns = inspect(engine).get_columns("users")
    except SQLAlchemyError:
        return False
    column_names = {str(column.get("name")) for column in columns}
    return required.issubset(column_names)


@lru_cache(maxsize=1)
def user_admin_column_available() -> bool:
    try:
        columns = inspect(engine).get_columns("users")
    except SQLAlchemyError:
        return False
    return any(str(column.get("name")) == "is_admin" for column in columns)


def load_current_user(db: Session, user_id: int) -> User | None:
    if user_logo_column_available() and user_onboarding_columns_available() and user_admin_column_available():
        return db.get(User, user_id)
    options = [
        User.id,
        User.email,
        User.full_name,
        User.email_verified,
        User.auth_provider,
        User.last_login_at,
        User.is_active,
        User.is_deleted,
        User.deleted_at,
        User.created_at,
        User.updated_at,
    ]
    if user_onboarding_columns_available():
        options.extend([User.onboarding_completed, User.user_type, User.goals, User.platforms])
    if user_admin_column_available():
        options.append(User.is_admin)
    return (
        db.query(User)
        .options(
            load_only(*options)
        )
        .filter(User.id == user_id)
        .first()
    )


def load_user_by_email(db: Session, email: str) -> User | None:
    query = db.query(User).filter(User.email == email)
    if user_logo_column_available() and user_onboarding_columns_available() and user_admin_column_available():
        return query.first()
    options = [
        User.id,
        User.email,
        User.password_hash,
        User.full_name,
        User.email_verified,
        User.auth_provider,
        User.last_login_at,
        User.is_active,
        User.is_deleted,
        User.deleted_at,
        User.created_at,
        User.updated_at,
    ]
    if user_onboarding_columns_available():
        options.extend([User.onboarding_completed, User.user_type, User.goals, User.platforms])
    if user_admin_column_available():
        options.append(User.is_admin)
    return query.options(load_only(*options)).first()


def load_user_by_google_sub(db: Session, google_sub: str) -> User | None:
    query = db.query(User).filter(User.google_sub == google_sub)
    if user_onboarding_columns_available() and user_logo_column_available() and user_admin_column_available():
        return query.first()
    options = [
        User.id,
        User.email,
        User.full_name,
        User.email_verified,
        User.auth_provider,
        User.google_sub,
        User.facebook_sub,
        User.last_login_at,
        User.is_active,
        User.is_deleted,
        User.deleted_at,
        User.created_at,
        User.updated_at,
    ]
    if user_onboarding_columns_available():
        options.extend([User.onboarding_completed, User.user_type, User.goals, User.platforms])
    if user_admin_column_available():
        options.append(User.is_admin)
    return query.options(load_only(*options)).first()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials is not None else request.cookies.get("access_token")
    if not token:
        raise http_error(401, "missing_token", "Authorization token required.")
    try:
        subject = get_subject(token)
    except TokenError:
        raise http_error(401, "invalid_token", "Invalid or expired token.")
    user = load_current_user(db, int(subject))
    if not user or not user.is_active or getattr(user, "is_deleted", False) or not getattr(user, "email_verified", False):
        raise http_error(401, "invalid_user", "User not found or inactive.")
    return user


def get_optional_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    token = credentials.credentials if credentials is not None else request.cookies.get("access_token")
    if not token:
        return None
    try:
        subject = get_subject(token)
    except TokenError:
        return None
    user = load_current_user(db, int(subject))
    if not user or not user.is_active or getattr(user, "is_deleted", False) or not getattr(user, "email_verified", False):
        return None
    return user


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not getattr(current_user, "is_admin", False):
        raise http_error(403, "forbidden", "Admin access required.")
    return current_user


def get_current_user_for_report_read(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials is not None else request.cookies.get("access_token")
    if not token:
        raise http_error(401, "missing_token", "Authorization token required.")
    try:
        payload = get_token_payload(token)
    except TokenError:
        raise http_error(401, "invalid_token", "Invalid or expired token.")

    subject = str(payload.get("sub"))
    token_use = str(payload.get("token_use") or "access")
    user = load_current_user(db, int(subject))
    if not user or not user.is_active or getattr(user, "is_deleted", False) or not getattr(user, "email_verified", False):
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
