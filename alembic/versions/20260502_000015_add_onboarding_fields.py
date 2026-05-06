"""add onboarding fields

Revision ID: 20260502_000015
Revises: 20260502_000014
Create Date: 2026-05-02 00:00:15

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260502_000015"
down_revision = "20260502_000014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("user_type", sa.String(length=50)))
    op.add_column(
        "users",
        sa.Column(
            "goals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "platforms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.execute(sa.text("UPDATE users SET onboarding_completed = false WHERE onboarding_completed IS NULL"))
    op.execute(sa.text("UPDATE users SET goals = '[]'::jsonb WHERE goals IS NULL"))
    op.execute(sa.text("UPDATE users SET platforms = '[]'::jsonb WHERE platforms IS NULL"))


def downgrade() -> None:
    op.drop_column("users", "platforms")
    op.drop_column("users", "goals")
    op.drop_column("users", "user_type")
    op.drop_column("users", "onboarding_completed")
