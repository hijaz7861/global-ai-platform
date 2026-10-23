"""Add tenant-scoped catalog listings."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_catalog_listings"
down_revision: Union[str, None] = "0001_identity_tenancy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=5000), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price_minor > 0", name="ck_listings_price_positive"),
        sa.CheckConstraint("length(currency) = 3", name="ck_listings_currency_length"),
    )
    op.create_index("ix_listings_organization_id", "listings", ["organization_id"])
    op.create_index("ix_listings_status_created", "listings", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_listings_status_created", table_name="listings")
    op.drop_index("ix_listings_organization_id", table_name="listings")
    op.drop_table("listings")
