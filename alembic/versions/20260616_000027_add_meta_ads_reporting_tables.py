"""add meta ads reporting tables

Revision ID: 20260616_000027
Revises: 20260523_000026
Create Date: 2026-06-16 00:00:27

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260616_000027"
down_revision = "20260523_000026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_ad_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.String(length=255), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("timezone_name", sa.String(length=100), nullable=True),
        sa.Column("account_status", sa.String(length=50), nullable=True),
        sa.Column("business_id", sa.String(length=255), nullable=True),
        sa.Column("business_name", sa.String(length=255), nullable=True),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], name="fk_meta_ad_accounts_integration_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_meta_ad_accounts_workspace_id"),
        sa.UniqueConstraint("integration_id", "account_id", name="uq_meta_ad_accounts_integration_account"),
    )
    op.create_index("ix_meta_ad_accounts_integration_id", "meta_ad_accounts", ["integration_id"])
    op.create_index("ix_meta_ad_accounts_workspace_id", "meta_ad_accounts", ["workspace_id"])

    op.create_table(
        "meta_ads_insights_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("meta_ad_account_id", sa.Integer(), nullable=False),
        sa.Column("date_start", sa.Date(), nullable=False),
        sa.Column("date_stop", sa.Date(), nullable=False),
        sa.Column("spend", sa.Numeric(14, 4), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("reach", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("inline_link_clicks", sa.Integer(), nullable=True),
        sa.Column("ctr", sa.Numeric(12, 4), nullable=True),
        sa.Column("cpc", sa.Numeric(14, 4), nullable=True),
        sa.Column("cpm", sa.Numeric(14, 4), nullable=True),
        sa.Column("frequency", sa.Numeric(12, 4), nullable=True),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost_per_action_type", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("campaign_id", sa.String(length=255), nullable=True),
        sa.Column("campaign_name", sa.String(length=255), nullable=True),
        sa.Column("adset_id", sa.String(length=255), nullable=True),
        sa.Column("adset_name", sa.String(length=255), nullable=True),
        sa.Column("ad_id", sa.String(length=255), nullable=True),
        sa.Column("ad_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], name="fk_meta_ads_insights_daily_integration_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_meta_ads_insights_daily_workspace_id"),
        sa.ForeignKeyConstraint(
            ["meta_ad_account_id"],
            ["meta_ad_accounts.id"],
            name="fk_meta_ads_insights_daily_meta_ad_account_id",
        ),
        sa.UniqueConstraint(
            "meta_ad_account_id",
            "date_start",
            "campaign_id",
            "adset_id",
            "ad_id",
            name="uq_meta_ads_insights_daily_grain",
        ),
    )
    op.create_index("ix_meta_ads_insights_daily_integration_id", "meta_ads_insights_daily", ["integration_id"])
    op.create_index("ix_meta_ads_insights_daily_workspace_id", "meta_ads_insights_daily", ["workspace_id"])
    op.create_index(
        "ix_meta_ads_insights_daily_meta_ad_account_id",
        "meta_ads_insights_daily",
        ["meta_ad_account_id"],
    )
    op.create_index("ix_meta_ads_insights_daily_date_start", "meta_ads_insights_daily", ["date_start"])


def downgrade() -> None:
    op.drop_index("ix_meta_ads_insights_daily_date_start", table_name="meta_ads_insights_daily")
    op.drop_index("ix_meta_ads_insights_daily_meta_ad_account_id", table_name="meta_ads_insights_daily")
    op.drop_index("ix_meta_ads_insights_daily_workspace_id", table_name="meta_ads_insights_daily")
    op.drop_index("ix_meta_ads_insights_daily_integration_id", table_name="meta_ads_insights_daily")
    op.drop_table("meta_ads_insights_daily")
    op.drop_index("ix_meta_ad_accounts_workspace_id", table_name="meta_ad_accounts")
    op.drop_index("ix_meta_ad_accounts_integration_id", table_name="meta_ad_accounts")
    op.drop_table("meta_ad_accounts")
