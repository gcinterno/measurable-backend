"""add email verification auth fields

Revision ID: 20260502_000014
Revises: 20260428_000013
Create Date: 2026-05-02 00:00:14

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260502_000014"
down_revision = "20260428_000013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("auth_provider", sa.String(length=50), nullable=False, server_default="email"),
    )
    op.add_column("users", sa.Column("google_sub", sa.String(length=255)))
    op.add_column("users", sa.Column("facebook_sub", sa.String(length=255)))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))

    op.execute(sa.text("UPDATE users SET email_verified = true"))

    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False, server_default="email_verification"),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_email_verification_codes_user_id", ondelete="CASCADE"),
    )
    op.create_index("ix_email_verification_codes_user_id", "email_verification_codes", ["user_id"])
    op.create_index("ix_email_verification_codes_purpose", "email_verification_codes", ["purpose"])
    op.create_index(
        "ix_email_verification_codes_expires_at",
        "email_verification_codes",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_verification_codes_expires_at", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_purpose", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_user_id", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")

    op.drop_column("users", "last_login_at")
    op.drop_column("users", "facebook_sub")
    op.drop_column("users", "google_sub")
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "email_verified")
