"""local streams and offer links for the editor

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stream",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("campaign_id", sa.Integer, nullable=False, index=True),
        sa.Column("keitaro_id", sa.Integer, nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "stream_offer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stream_id", sa.Integer, sa.ForeignKey("stream.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("share", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pin_value", sa.Integer),
        sa.Column("is_new", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("synced_status", sa.String(16), nullable=False),
        sa.Column("synced_share", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("stream_id", "offer_id"),
    )
    with op.batch_alter_table("action_log") as b:
        b.alter_column("stream_id", existing_type=sa.Integer, nullable=True)
        b.alter_column("offer_id", existing_type=sa.Integer, nullable=True)


def downgrade():
    op.drop_table("stream_offer")
    op.drop_table("stream")
