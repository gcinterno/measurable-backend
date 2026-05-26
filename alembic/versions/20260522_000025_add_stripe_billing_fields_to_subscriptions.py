"""add stripe billing fields to subscriptions

Revision ID: 20260522_000025
Revises: 20260522_000024
Create Date: 2026-05-22 00:00:25

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260522_000025"
down_revision = "20260522_000024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("billing_status", sa.String(length=50), nullable=True))
    op.add_column("subscriptions", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    op.add_column("subscriptions", sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True))
    op.add_column("subscriptions", sa.Column("stripe_price_id", sa.String(length=255), nullable=True))
    op.add_column("subscriptions", sa.Column("cancel_at_period_end", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("reports_limit_monthly", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("reports_limit_is_temporary", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("slides_per_report_limit", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("platform_report_type", sa.String(length=100), nullable=True))
    op.add_column("subscriptions", sa.Column("ai_chat_with_data", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("storage_limit_gb", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("export_pdf", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("export_pptx", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("brand_personalization", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("measurable_watermark", sa.Boolean(), nullable=True))
    op.add_column("subscriptions", sa.Column("scheduled_reports_limit", sa.Integer(), nullable=True))
    op.add_column("subscriptions", sa.Column("trial_new_features", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "trial_new_features")
    op.drop_column("subscriptions", "scheduled_reports_limit")
    op.drop_column("subscriptions", "measurable_watermark")
    op.drop_column("subscriptions", "brand_personalization")
    op.drop_column("subscriptions", "export_pptx")
    op.drop_column("subscriptions", "export_pdf")
    op.drop_column("subscriptions", "storage_limit_gb")
    op.drop_column("subscriptions", "ai_chat_with_data")
    op.drop_column("subscriptions", "platform_report_type")
    op.drop_column("subscriptions", "slides_per_report_limit")
    op.drop_column("subscriptions", "reports_limit_is_temporary")
    op.drop_column("subscriptions", "reports_limit_monthly")
    op.drop_column("subscriptions", "cancel_at_period_end")
    op.drop_column("subscriptions", "stripe_price_id")
    op.drop_column("subscriptions", "stripe_subscription_id")
    op.drop_column("subscriptions", "stripe_customer_id")
    op.drop_column("subscriptions", "billing_status")
