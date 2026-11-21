"""Add tenant-scoped accounts and balanced journal entries."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_double_entry_ledger"
down_revision: Union[str, None] = "0004_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("account_type", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "code", "currency", name="uq_accounts_org_code_currency"),
        sa.CheckConstraint("length(currency) = 3", name="ck_accounts_currency_length"),
        sa.CheckConstraint("account_type IN ('asset','liability','equity','revenue','expense')", name="ck_accounts_type"),
    )
    op.create_index("ix_accounts_org_active", "accounts", ["organization_id", "is_active"])

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="posted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_journal_org_idempotency"),
        sa.CheckConstraint("total_minor > 0", name="ck_journal_total_positive"),
        sa.CheckConstraint("status = 'posted'", name="ck_journal_posted_only"),
    )
    op.create_index("ix_journal_org_created", "journal_entries", ["organization_id", "created_at"])

    op.create_table(
        "journal_lines",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("journal_entry_id", sa.String(length=36), sa.ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("debit_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credit_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("debit_minor >= 0 AND credit_minor >= 0", name="ck_journal_lines_nonnegative"),
        sa.CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)",
            name="ck_journal_lines_one_side",
        ),
    )
    op.create_index("ix_journal_lines_entry", "journal_lines", ["journal_entry_id"])
    op.create_index("ix_journal_lines_account", "journal_lines", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_lines_account", table_name="journal_lines")
    op.drop_index("ix_journal_lines_entry", table_name="journal_lines")
    op.drop_table("journal_lines")
    op.drop_index("ix_journal_org_created", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_index("ix_accounts_org_active", table_name="accounts")
    op.drop_table("accounts")
