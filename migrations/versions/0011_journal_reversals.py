"""Support immutable journal reversals."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_journal_reversals"
down_revision: Union[str, None] = "0010_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("journal_entries") as batch_op:
        batch_op.add_column(sa.Column("reversal_of_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_journal_entries_reversal_of_id_journal_entries",
            "journal_entries",
            ["reversal_of_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint("uq_journal_reversal_source", ["reversal_of_id"])


def downgrade() -> None:
    with op.batch_alter_table("journal_entries") as batch_op:
        batch_op.drop_constraint("uq_journal_reversal_source", type_="unique")
        batch_op.drop_constraint("fk_journal_entries_reversal_of_id_journal_entries", type_="foreignkey")
        batch_op.drop_column("reversal_of_id")
