"""add shopify tables

Revision ID: 20260616_000028
Revises: 20260616_000027
Create Date: 2026-06-16 00:00:28

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260616_000028"
down_revision = "20260616_000027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shopify_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("shop_domain", sa.String(length=255), nullable=False),
        sa.Column("shop_name", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="disconnected"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_shopify_connections_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_shopify_connections_workspace_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], name="fk_shopify_connections_integration_id", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "shop_domain", name="uq_shopify_connections_user_shop_domain"),
        sa.UniqueConstraint("integration_id", name="uq_shopify_connections_integration_id"),
    )
    op.create_index("ix_shopify_connections_user_id", "shopify_connections", ["user_id"])
    op.create_index("ix_shopify_connections_workspace_id", "shopify_connections", ["workspace_id"])
    op.create_index("ix_shopify_connections_shop_domain", "shopify_connections", ["shop_domain"])

    op.create_table(
        "shopify_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("timeframe", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_shopify_snapshots_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_shopify_snapshots_workspace_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["shopify_connections.id"], name="fk_shopify_snapshots_connection_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_shopify_snapshots_dataset_id", ondelete="SET NULL"),
    )
    op.create_index("ix_shopify_snapshots_user_id", "shopify_snapshots", ["user_id"])
    op.create_index("ix_shopify_snapshots_workspace_id", "shopify_snapshots", ["workspace_id"])
    op.create_index("ix_shopify_snapshots_connection_id", "shopify_snapshots", ["connection_id"])
    op.create_index("ix_shopify_snapshots_dataset_id", "shopify_snapshots", ["dataset_id"])

    op.create_table(
        "shopify_oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("shop_domain", sa.String(length=255), nullable=False),
        sa.Column("state_token", sa.String(length=1024), nullable=False),
        sa.Column("purpose", sa.String(length=50), nullable=False, server_default="shopify_oauth"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_shopify_oauth_states_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_shopify_oauth_states_workspace_id", ondelete="CASCADE"),
        sa.UniqueConstraint("state_token", name="uq_shopify_oauth_states_state_token"),
    )
    op.create_index("ix_shopify_oauth_states_user_id", "shopify_oauth_states", ["user_id"])
    op.create_index("ix_shopify_oauth_states_workspace_id", "shopify_oauth_states", ["workspace_id"])
    op.create_index("ix_shopify_oauth_states_shop_domain", "shopify_oauth_states", ["shop_domain"])
    op.create_index("ix_shopify_oauth_states_expires_at", "shopify_oauth_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_shopify_oauth_states_expires_at", table_name="shopify_oauth_states")
    op.drop_index("ix_shopify_oauth_states_shop_domain", table_name="shopify_oauth_states")
    op.drop_index("ix_shopify_oauth_states_workspace_id", table_name="shopify_oauth_states")
    op.drop_index("ix_shopify_oauth_states_user_id", table_name="shopify_oauth_states")
    op.drop_table("shopify_oauth_states")

    op.drop_index("ix_shopify_snapshots_dataset_id", table_name="shopify_snapshots")
    op.drop_index("ix_shopify_snapshots_connection_id", table_name="shopify_snapshots")
    op.drop_index("ix_shopify_snapshots_workspace_id", table_name="shopify_snapshots")
    op.drop_index("ix_shopify_snapshots_user_id", table_name="shopify_snapshots")
    op.drop_table("shopify_snapshots")

    op.drop_index("ix_shopify_connections_shop_domain", table_name="shopify_connections")
    op.drop_index("ix_shopify_connections_workspace_id", table_name="shopify_connections")
    op.drop_index("ix_shopify_connections_user_id", table_name="shopify_connections")
    op.drop_table("shopify_connections")
