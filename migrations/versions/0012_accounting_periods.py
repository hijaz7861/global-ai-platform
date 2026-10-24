"""Add organization accounting periods."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_accounting_periods"
down_revision: Union[str, None] = "0011_journal_reversals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounting_periods",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("closed_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_accounting_period_org_name"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_accounting_period_date_order"),
        sa.CheckConstraint("status IN ('open','closed')", name="ck_accounting_period_status"),
    )
    op.create_index(
        "ix_accounting_period_org_dates",
        "accounting_periods",
        ["organization_id", "starts_on", "ends_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_accounting_period_org_dates", table_name="accounting_periods")
    op.drop_table("accounting_periods")
