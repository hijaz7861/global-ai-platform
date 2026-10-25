"""Add manual bank-statement reconciliation workflow."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026_bank_reconciliation"
down_revision: Union[str, None] = "0025_accounting_period_overlap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_reconciliation_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("statement_start_date", sa.Date(), nullable=False),
        sa.Column("statement_end_date", sa.Date(), nullable=False),
        sa.Column("opening_balance_minor", sa.BigInteger(), nullable=False),
        sa.Column("closing_balance_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open','completed')", name="ck_bank_reconciliation_status"),
        sa.CheckConstraint("statement_end_date >= statement_start_date", name="ck_bank_reconciliation_dates"),
        sa.UniqueConstraint(
            "organization_id", "account_id", "statement_start_date", "statement_end_date",
            name="uq_bank_reconciliation_account_window",
        ),
    )
    op.create_index(
        "ix_bank_reconciliation_org_created",
        "bank_reconciliation_sessions",
        ["organization_id", "created_at"],
    )
    op.create_table(
        "bank_reconciliation_lines",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("bank_reconciliation_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("statement_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("external_reference", sa.String(length=128), nullable=True),
        sa.Column("matched_journal_line_id", sa.String(length=36), sa.ForeignKey("journal_lines.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor != 0", name="ck_bank_reconciliation_line_nonzero_amount"),
        sa.UniqueConstraint("matched_journal_line_id", name="uq_bank_reconciliation_matched_journal_line"),
    )
    op.create_index(
        "ix_bank_reconciliation_lines_session_date",
        "bank_reconciliation_lines",
        ["session_id", "statement_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_bank_reconciliation_lines_session_date", table_name="bank_reconciliation_lines")
    op.drop_table("bank_reconciliation_lines")
    op.drop_index("ix_bank_reconciliation_org_created", table_name="bank_reconciliation_sessions")
    op.drop_table("bank_reconciliation_sessions")
