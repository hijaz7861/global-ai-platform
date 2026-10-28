"""Persist single-use AI sandbox evidence nonces."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_ai_evidence_nonces"
down_revision: Union[str, None] = "0028_reconciliation_imports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_evidence_nonces",
        sa.Column("nonce_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_evidence_nonces_expires_at",
        "ai_evidence_nonces",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_evidence_nonces_expires_at", table_name="ai_evidence_nonces")
    op.drop_table("ai_evidence_nonces")
