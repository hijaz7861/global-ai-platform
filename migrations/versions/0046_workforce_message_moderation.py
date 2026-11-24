"""Add participant reports and moderator-controlled message visibility."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0046_workforce_moderation"
down_revision: Union[str, None] = "0045_workforce_msg_encrypt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("workforce_service_messages") as batch:
        batch.add_column(
            sa.Column(
                "moderation_status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'visible'"),
            )
        )
        batch.create_check_constraint(
            "ck_workforce_service_message_moderation_status",
            "moderation_status IN ('visible','hidden')",
        )

    op.create_table(
        "workforce_message_reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("workforce_service_messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reporter_user_id",
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
            "reason_code IN ('spam','harassment','contact_sharing','scam_or_fraud','unsafe_content','other')",
            name="ck_workforce_message_report_reason",
        ),
        sa.CheckConstraint(
            "status IN ('open','dismissed','actioned')",
            name="ck_workforce_message_report_status",
        ),
        sa.UniqueConstraint(
            "message_id",
            "reporter_user_id",
            name="uq_workforce_message_reporter_message",
        ),
    )
    op.create_index(
        "ix_workforce_message_reports_status_created",
        "workforce_message_reports",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_workforce_message_reports_message_created",
        "workforce_message_reports",
        ["message_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    report_count = connection.execute(
        sa.text("SELECT count(*) FROM workforce_message_reports")
    ).scalar_one()
    hidden_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM workforce_service_messages "
            "WHERE moderation_status = 'hidden'"
        )
    ).scalar_one()
    if report_count or hidden_count:
        raise RuntimeError(
            "Cannot downgrade while workforce message reports or hidden messages exist"
        )

    op.drop_index(
        "ix_workforce_message_reports_message_created",
        table_name="workforce_message_reports",
    )
    op.drop_index(
        "ix_workforce_message_reports_status_created",
        table_name="workforce_message_reports",
    )
    op.drop_table("workforce_message_reports")
    with op.batch_alter_table("workforce_service_messages") as batch:
        batch.drop_constraint(
            "ck_workforce_service_message_moderation_status",
            type_="check",
        )
        batch.drop_column("moderation_status")
