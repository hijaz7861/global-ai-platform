"""Add tenant-scoped BOQ-based construction quotes and line snapshots."""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0038_construction_quotes"
down_revision: Union[str, None] = "0037_construction_operations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "construction_quotes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quote_no", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("boq_subtotal_minor", sa.BigInteger(), nullable=False),
        sa.Column("overhead_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("overhead_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("profit_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("profit_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("discount_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("discount_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_pct", sa.Numeric(7, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("validity_days", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("terms", sa.String(3000), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "organization_id", name="uq_construction_quotes_id_org"),
        sa.UniqueConstraint("organization_id", "quote_no", name="uq_construction_quotes_org_quote_no"),
        sa.ForeignKeyConstraint(["project_id", "organization_id"],
            ["construction_projects.id", "construction_projects.organization_id"],
            ondelete="RESTRICT", name="fk_construction_quotes_project_org"),
        sa.CheckConstraint("status IN ('draft','sent','accepted','rejected','expired')",
                           name="ck_construction_quotes_status"),
        sa.CheckConstraint("currency = 'PKR'", name="ck_construction_quotes_currency_pkr"),
        sa.CheckConstraint("validity_days BETWEEN 1 AND 365", name="ck_construction_quotes_validity"),
        sa.CheckConstraint("overhead_pct BETWEEN 0 AND 100", name="ck_construction_quotes_overhead_pct"),
        sa.CheckConstraint("profit_pct BETWEEN 0 AND 100", name="ck_construction_quotes_profit_pct"),
        sa.CheckConstraint("discount_pct BETWEEN 0 AND 100", name="ck_construction_quotes_discount_pct"),
        sa.CheckConstraint("tax_pct BETWEEN 0 AND 100", name="ck_construction_quotes_tax_pct"),
        sa.CheckConstraint("boq_subtotal_minor >= 0 AND overhead_amount_minor >= 0 AND profit_amount_minor >= 0",
                           name="ck_construction_quotes_base_amounts_nonnegative"),
        sa.CheckConstraint("discount_amount_minor >= 0 AND tax_amount_minor >= 0 AND total_minor >= 0",
                           name="ck_construction_quotes_adjustments_nonnegative"),
    )
    op.create_index("ix_construction_quotes_org_project_created", "construction_quotes",
                    ["organization_id", "project_id", "created_at"])

    op.create_table(
        "construction_quote_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("quote_id", sa.String(36), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("source_boq_line_number", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("item_name", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_rate_minor", sa.BigInteger(), nullable=False),
        sa.Column("line_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["quote_id", "organization_id"],
            ["construction_quotes.id", "construction_quotes.organization_id"],
            ondelete="RESTRICT", name="fk_construction_quote_lines_quote_org"),
        sa.UniqueConstraint("quote_id", "line_number", name="uq_construction_quote_lines_quote_line"),
        sa.CheckConstraint("line_number BETWEEN 1 AND 10000", name="ck_construction_quote_lines_line_number"),
        sa.CheckConstraint("source_boq_line_number IS NULL OR source_boq_line_number BETWEEN 1 AND 10000",
                           name="ck_construction_quote_lines_source_line"),
        sa.CheckConstraint("quantity > 0", name="ck_construction_quote_lines_quantity_positive"),
        sa.CheckConstraint("unit_rate_minor >= 0 AND line_total_minor >= 0",
                           name="ck_construction_quote_lines_amounts_nonnegative"),
    )
    op.create_index("ix_construction_quote_lines_org_quote", "construction_quote_lines",
                    ["organization_id", "quote_id", "line_number"])


def downgrade() -> None:
    op.drop_index("ix_construction_quote_lines_org_quote", table_name="construction_quote_lines")
    op.drop_table("construction_quote_lines")
    op.drop_index("ix_construction_quotes_org_project_created", table_name="construction_quotes")
    op.drop_table("construction_quotes")
