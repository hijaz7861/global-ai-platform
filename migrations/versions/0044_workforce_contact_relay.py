"""Add participant-only workforce service-request messages."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0044_workforce_contact_relay"
down_revision: Union[str, None] = "0043_workforce_service_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workforce_service_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "service_request_id",
            sa.String(36),
            sa.ForeignKey("workforce_service_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sender_side", sa.String(12), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sender_side IN ('client','provider')",
            name="ck_workforce_service_message_sender_side",
        ),
        sa.CheckConstraint(
            "length(body) BETWEEN 1 AND 2000",
            name="ck_workforce_service_message_body_length",
        ),
    )
    op.create_index(
        "ix_workforce_service_message_request_created",
        "workforce_service_messages",
        ["service_request_id", "created_at", "id"],
    )
    op.create_index(
        "ix_workforce_service_message_sender_created",
        "workforce_service_messages",
        ["sender_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workforce_service_message_sender_created",
        table_name="workforce_service_messages",
    )
    op.drop_index(
        "ix_workforce_service_message_request_created",
        table_name="workforce_service_messages",
    )
    op.drop_table("workforce_service_messages")
