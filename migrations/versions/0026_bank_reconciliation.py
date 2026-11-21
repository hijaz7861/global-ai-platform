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
        sa.UniqueConstraint("session_id", "external_reference", name="uq_bank_reconciliation_external_reference"),
    )
    op.create_index(
        "ix_bank_reconciliation_lines_session_date",
        "bank_reconciliation_lines",
        ["session_id", "statement_date"],
    )


    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_overlapping_bank_reconciliation() RETURNS trigger AS $function$
            BEGIN
                PERFORM id FROM accounts WHERE id = NEW.account_id FOR UPDATE;
                IF EXISTS (
                    SELECT 1 FROM bank_reconciliation_sessions
                    WHERE account_id = NEW.account_id
                      AND id <> NEW.id
                      AND statement_start_date <= NEW.statement_end_date
                      AND statement_end_date >= NEW.statement_start_date
                ) THEN
                    RAISE EXCEPTION 'bank reconciliation statement windows must not overlap';
                END IF;
                RETURN NEW;
            END;
            $function$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_bank_reconciliation_no_overlap
            BEFORE INSERT OR UPDATE ON bank_reconciliation_sessions
            FOR EACH ROW EXECUTE FUNCTION reject_overlapping_bank_reconciliation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_bank_reconciliation_no_overlap_insert
            BEFORE INSERT ON bank_reconciliation_sessions
            WHEN EXISTS (
                SELECT 1 FROM bank_reconciliation_sessions
                WHERE account_id = NEW.account_id
                  AND statement_start_date <= NEW.statement_end_date
                  AND statement_end_date >= NEW.statement_start_date
            )
            BEGIN
                SELECT RAISE(ABORT, 'bank reconciliation statement windows must not overlap');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_bank_reconciliation_no_overlap_update
            BEFORE UPDATE ON bank_reconciliation_sessions
            WHEN EXISTS (
                SELECT 1 FROM bank_reconciliation_sessions
                WHERE account_id = NEW.account_id
                  AND id <> NEW.id
                  AND statement_start_date <= NEW.statement_end_date
                  AND statement_end_date >= NEW.statement_start_date
            )
            BEGIN
                SELECT RAISE(ABORT, 'bank reconciliation statement windows must not overlap');
            END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_bank_reconciliation_no_overlap ON bank_reconciliation_sessions")
        op.execute("DROP FUNCTION IF EXISTS reject_overlapping_bank_reconciliation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_bank_reconciliation_no_overlap_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_bank_reconciliation_no_overlap_update")
    op.drop_index("ix_bank_reconciliation_lines_session_date", table_name="bank_reconciliation_lines")
    op.drop_table("bank_reconciliation_lines")
    op.drop_index("ix_bank_reconciliation_org_created", table_name="bank_reconciliation_sessions")
    op.drop_table("bank_reconciliation_sessions")
