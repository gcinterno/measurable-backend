from __future__ import annotations

import sys

from sqlalchemy import inspect, text

from app.db import SessionLocal
from app.models import User


TARGET_EMAIL = "gcinterno@atriamarketing.com"


def main() -> int:
    db = SessionLocal()
    try:
        bind = db.get_bind()
        column_names = {str(column.get("name")) for column in inspect(bind).get_columns("users")}
        if "is_admin" not in column_names:
            print("INFO: users.is_admin missing; adding column before promotion")
            db.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false"))
            db.commit()

        user = db.query(User).filter(User.email == TARGET_EMAIL).one_or_none()
        if user is None:
            print("User not found")
            return 1

        user.is_admin = True
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"User promoted to admin: {user.email}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERROR: failed to promote {TARGET_EMAIL}: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
