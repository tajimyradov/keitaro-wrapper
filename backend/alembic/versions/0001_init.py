"""init: campaign_log, action_log

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campaign_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("keitaro_id", sa.Integer, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("geo", sa.String(255), nullable=False),
        sa.Column("offer_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "action_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("campaign_id", sa.Integer, nullable=False, index=True),
        sa.Column("stream_id", sa.Integer, nullable=False),
        sa.Column("offer_id", sa.Integer, nullable=False),
        sa.Column("details", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("action_log")
    op.drop_table("campaign_log")
