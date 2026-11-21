"""Track bank reconciliation CSV import provenance."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028_reconciliation_imports"
down_revision: Union[str, None] = "0027_journal_period_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_reconciliation_import_batches",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("bank_reconciliation_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "content_sha256", name="uq_recon_import_session_digest"),
        sa.CheckConstraint("row_count >= 0", name="ck_recon_import_row_count"),
    )
    op.create_index("ix_recon_import_session_created", "bank_reconciliation_import_batches", ["session_id", "created_at"])
    with op.batch_alter_table("bank_reconciliation_lines") as batch_op:
        batch_op.add_column(sa.Column("import_batch_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_reconciliation_line_import_batch",
            "bank_reconciliation_import_batches",
            ["import_batch_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_recon_lines_import_batch", "bank_reconciliation_lines", ["import_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_recon_lines_import_batch", table_name="bank_reconciliation_lines")
    with op.batch_alter_table("bank_reconciliation_lines") as batch_op:
        batch_op.drop_constraint("fk_reconciliation_line_import_batch", type_="foreignkey")
        batch_op.drop_column("import_batch_id")
    op.drop_index("ix_recon_import_session_created", table_name="bank_reconciliation_import_batches")
    op.drop_table("bank_reconciliation_import_batches")
