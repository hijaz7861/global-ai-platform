"""Add sender-only appeals for moderator-hidden workforce messages."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0047_workforce_message_appeals"
down_revision: Union[str, None] = "0046_workforce_moderation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workforce_message_appeals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("workforce_service_messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "appellant_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "reviewed_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "reason_code IN ('decision_incorrect','context_missing','identity_misuse','other')",
            name="ck_workforce_message_appeal_reason",
        ),
        sa.CheckConstraint(
            "status IN ('open','upheld','denied')",
            name="ck_workforce_message_appeal_status",
        ),
        sa.CheckConstraint(
            "status = 'open' OR reviewed_at IS NOT NULL",
            name="ck_workforce_message_appeal_reviewed_at",
        ),
        sa.UniqueConstraint(
            "message_id",
            "appellant_user_id",
            name="uq_workforce_message_appeal_message_appellant",
        ),
    )
    op.create_index(
        "ix_workforce_message_appeals_status_created",
        "workforce_message_appeals",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_workforce_message_appeals_message_created",
        "workforce_message_appeals",
        ["message_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    appeal_count = connection.execute(
        sa.text("SELECT count(*) FROM workforce_message_appeals")
    ).scalar_one()
    if appeal_count:
        raise RuntimeError(
            "Cannot downgrade while workforce message appeals exist"
        )
    op.drop_index(
        "ix_workforce_message_appeals_message_created",
        table_name="workforce_message_appeals",
    )
    op.drop_index(
        "ix_workforce_message_appeals_status_created",
        table_name="workforce_message_appeals",
    )
    op.drop_table("workforce_message_appeals")
