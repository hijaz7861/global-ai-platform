"""Add append-only supplier quote revision history."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023_supplier_quote_revisions"
down_revision: Union[str, None] = "0022_purchase_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supplier_quote_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("supplier_quote_id", sa.String(length=36), sa.ForeignKey("supplier_quotes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("supplier_quote_id", "revision_number", name="uq_supplier_quote_revision_number"),
        sa.CheckConstraint("revision_number > 0", name="ck_supplier_quote_revision_number_positive"),
        sa.CheckConstraint("unit_price_minor > 0", name="ck_supplier_quote_revision_price_positive"),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_supplier_quote_revision_lead_time_nonnegative"),
        sa.CheckConstraint("status IN ('submitted','withdrawn')", name="ck_supplier_quote_revisions_status"),
    )
    # Preserve the original submitted terms for quotes that predate revision history.
    op.execute(
        sa.text(
            """
            INSERT INTO supplier_quote_revisions
                (id, supplier_quote_id, revision_number, status, unit_price_minor,
                 currency, lead_time_days, notes, actor_user_id, created_at)
            SELECT id, id, 1, 'submitted', unit_price_minor, currency,
                   lead_time_days, notes, created_by_user_id, created_at
            FROM supplier_quotes
            """
        )
    )
    op.create_index(
        "ix_supplier_quote_revisions_quote_created",
        "supplier_quote_revisions",
        ["supplier_quote_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_quote_revisions_quote_created", table_name="supplier_quote_revisions")
    op.drop_table("supplier_quote_revisions")
