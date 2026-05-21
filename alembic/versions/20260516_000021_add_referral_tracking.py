"""add referral tracking

Revision ID: 20260516_000021
Revises: 20260511_000020
Create Date: 2026-05-16 00:00:21

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260516_000021"
down_revision = "20260511_000020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_partners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=True),
        sa.Column("commission_type", sa.String(length=50), nullable=True),
        sa.Column("commission_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("code", name="uq_referral_partners_code"),
    )
    op.create_index("ix_referral_partners_code", "referral_partners", ["code"])

    op.create_table(
        "referral_clicks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referral_code", sa.String(length=255), nullable=True),
        sa.Column("utm_source", sa.String(length=255), nullable=True),
        sa.Column("utm_medium", sa.String(length=255), nullable=True),
        sa.Column("utm_campaign", sa.String(length=255), nullable=True),
        sa.Column("utm_term", sa.String(length=255), nullable=True),
        sa.Column("utm_content", sa.String(length=255), nullable=True),
        sa.Column("landing_page", sa.String(length=2048), nullable=True),
        sa.Column("ip_hash", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_referral_clicks_referral_code", "referral_clicks", ["referral_code"])

    op.create_table(
        "user_attributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("first_referral_code", sa.String(length=255), nullable=True),
        sa.Column("last_referral_code", sa.String(length=255), nullable=True),
        sa.Column("utm_source", sa.String(length=255), nullable=True),
        sa.Column("utm_medium", sa.String(length=255), nullable=True),
        sa.Column("utm_campaign", sa.String(length=255), nullable=True),
        sa.Column("utm_term", sa.String(length=255), nullable=True),
        sa.Column("utm_content", sa.String(length=255), nullable=True),
        sa.Column("first_touch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_attributions_user_id", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_attributions_user_id"),
    )
    op.create_index("ix_user_attributions_first_referral_code", "user_attributions", ["first_referral_code"])
    op.create_index("ix_user_attributions_last_referral_code", "user_attributions", ["last_referral_code"])

    op.create_table(
        "referral_conversions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("referral_code", sa.String(length=255), nullable=True),
        sa.Column("conversion_type", sa.String(length=50), nullable=False),
        sa.Column("plan", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
        sa.Column("commission_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_referral_conversions_user_id", ondelete="CASCADE"),
    )
    op.create_index("ix_referral_conversions_referral_code", "referral_conversions", ["referral_code"])
    op.create_index("ix_referral_conversions_user_id", "referral_conversions", ["user_id"])
    op.create_index("ix_referral_conversions_conversion_type", "referral_conversions", ["conversion_type"])


def downgrade() -> None:
    op.drop_index("ix_referral_conversions_conversion_type", table_name="referral_conversions")
    op.drop_index("ix_referral_conversions_user_id", table_name="referral_conversions")
    op.drop_index("ix_referral_conversions_referral_code", table_name="referral_conversions")
    op.drop_table("referral_conversions")

    op.drop_index("ix_user_attributions_last_referral_code", table_name="user_attributions")
    op.drop_index("ix_user_attributions_first_referral_code", table_name="user_attributions")
    op.drop_table("user_attributions")

    op.drop_index("ix_referral_clicks_referral_code", table_name="referral_clicks")
    op.drop_table("referral_clicks")

    op.drop_index("ix_referral_partners_code", table_name="referral_partners")
    op.drop_table("referral_partners")
