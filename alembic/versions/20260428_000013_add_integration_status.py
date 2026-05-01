"""add integration status

Revision ID: 20260428_000013
Revises: 20260427_000012
Create Date: 2026-04-28 00:00:13

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260428_000013"
down_revision = "20260427_000012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="disconnected",
        ),
    )
    op.execute(
        """
        UPDATE integrations
        SET status = 'connected'
        WHERE EXISTS (
            SELECT 1
            FROM integration_accounts ia
            JOIN integration_tokens it ON it.account_id = ia.id
            WHERE ia.integration_id = integrations.id
        )
        """
    )


def downgrade() -> None:
    op.drop_column("integrations", "status")
