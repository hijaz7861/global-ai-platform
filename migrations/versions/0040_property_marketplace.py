"""Add portable property listings, bilateral sale and manual payment review."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0040_property_marketplace"
down_revision: Union[str, None] = "0039_construction_cashbook"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_phone_verifications",
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("phone_e164", sa.String(16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_user_phone_verifications_phone", "user_phone_verifications", ["phone_e164"]
    )

    op.create_table(
        "property_listings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("listing_code", sa.String(40), nullable=False, unique=True),
        sa.Column(
            "seller_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seller_phone", sa.String(16), nullable=False),
        sa.Column("property_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(140), nullable=False),
        sa.Column("description", sa.String(3000), nullable=False, server_default=""),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state_region", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=False, server_default="Pakistan"),
        sa.Column("locality", sa.String(160), nullable=True),
        sa.Column("address_private", sa.String(500), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("area_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("area_unit", sa.String(24), nullable=False),
        sa.Column("asking_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("dispute_status", sa.String(40), nullable=False),
        sa.Column("seller_declaration", sa.String(500), nullable=False),
        sa.Column("title_verification_status", sa.String(24), nullable=False, server_default="not_verified"),
        sa.Column("listing_fee_minor", sa.BigInteger(), nullable=False, server_default="50000"),
        sa.Column("listing_fee_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("listing_status", sa.String(24), nullable=False, server_default="pending_fee"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "property_type IN ('plot','house','apartment','commercial','agricultural','other')",
            name="ck_property_listings_type",
        ),
        sa.CheckConstraint("area_value > 0", name="ck_property_listings_area_positive"),
        sa.CheckConstraint(
            "asking_price_minor > 0 AND asking_price_minor <= 9000000000000000",
            name="ck_property_listings_price",
        ),
        sa.CheckConstraint(
            "listing_fee_minor > 0 AND listing_fee_minor <= 9000000000000000",
            name="ck_property_listings_fee",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_property_listings_currency_pkr"),
        sa.CheckConstraint(
            "dispute_status IN ('seller_declared_non_disputed','disputed','unknown')",
            name="ck_property_listings_dispute_status",
        ),
        sa.CheckConstraint(
            "title_verification_status IN ('not_verified','under_review','rejected')",
            name="ck_property_listings_title_status",
        ),
        sa.CheckConstraint(
            "listing_fee_status IN ('pending','paid','rejected')",
            name="ck_property_listings_fee_status",
        ),
        sa.CheckConstraint(
            "listing_status IN ('pending_fee','pending_review','published','rejected','sold','withdrawn')",
            name="ck_property_listings_status",
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_property_listings_coordinate_pair",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_property_listings_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_property_listings_longitude",
        ),
    )
    op.create_index(
        "ix_property_listings_public_search",
        "property_listings",
        ["listing_status", "country", "state_region", "city", "property_type", "created_at"],
    )
    op.create_index(
        "ix_property_listings_seller_created", "property_listings", ["seller_user_id", "created_at"]
    )

    op.create_table(
        "property_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_code", sa.String(40), nullable=False, unique=True),
        sa.Column(
            "listing_id", sa.String(36),
            sa.ForeignKey("property_listings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "buyer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "seller_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("final_sale_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("buyer_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seller_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("buyer_commission_minor", sa.BigInteger(), nullable=True),
        sa.Column("seller_commission_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("status", sa.String(24), nullable=False, server_default="interest"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("listing_id", "buyer_user_id", name="uq_property_transactions_listing_buyer"),
        sa.CheckConstraint("buyer_user_id <> seller_user_id", name="ck_property_transactions_distinct_parties"),
        sa.CheckConstraint(
            "status IN ('interest','awaiting_confirmation','sale_confirmed','commission_settled','cancelled','disputed')",
            name="ck_property_transactions_status",
        ),
        sa.CheckConstraint(
            "final_sale_price_minor IS NULL OR "
            "(final_sale_price_minor > 0 AND final_sale_price_minor <= 9000000000000000)",
            name="ck_property_transactions_price",
        ),
        sa.CheckConstraint(
            "buyer_commission_minor IS NULL OR buyer_commission_minor >= 0",
            name="ck_property_transactions_buyer_commission",
        ),
        sa.CheckConstraint(
            "seller_commission_minor IS NULL OR seller_commission_minor >= 0",
            name="ck_property_transactions_seller_commission",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_property_transactions_currency_pkr"),
    )
    op.create_index(
        "ix_property_transactions_buyer_status",
        "property_transactions", ["buyer_user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_property_transactions_seller_status",
        "property_transactions", ["seller_user_id", "status", "created_at"],
    )

    op.create_table(
        "property_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "listing_id", sa.String(36),
            sa.ForeignKey("property_listings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id", sa.String(36),
            sa.ForeignKey("property_transactions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "payer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payer_role", sa.String(8), nullable=False),
        sa.Column("payment_type", sa.String(24), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("payment_method", sa.String(24), nullable=True),
        sa.Column("payment_reference", sa.String(160), nullable=True),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("payer_role IN ('seller','buyer')", name="ck_property_payments_payer_role"),
        sa.CheckConstraint(
            "payment_type IN ('listing_fee','buyer_commission','seller_commission')",
            name="ck_property_payments_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','submitted','verified','rejected')",
            name="ck_property_payments_status",
        ),
        sa.CheckConstraint(
            "amount_minor > 0 AND amount_minor <= 9000000000000000",
            name="ck_property_payments_amount",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_property_payments_currency_pkr"),
        sa.CheckConstraint(
            "(payment_type = 'listing_fee' AND transaction_id IS NULL AND payer_role = 'seller') OR "
            "(payment_type = 'buyer_commission' AND transaction_id IS NOT NULL AND payer_role = 'buyer') OR "
            "(payment_type = 'seller_commission' AND transaction_id IS NOT NULL AND payer_role = 'seller')",
            name="ck_property_payments_type_party",
        ),
    )
    op.create_index(
        "ix_property_payments_payer_status",
        "property_payments", ["payer_user_id", "status", "created_at"],
    )
    op.create_index("ix_property_payments_status_created", "property_payments", ["status", "created_at"])
    op.create_index(
        "uq_property_listing_fee_payment", "property_payments", ["listing_id"],
        unique=True, sqlite_where=sa.text("payment_type = 'listing_fee'"),
        postgresql_where=sa.text("payment_type = 'listing_fee'"),
    )
    op.create_index(
        "uq_property_transaction_payment", "property_payments", ["transaction_id", "payment_type"],
        unique=True, sqlite_where=sa.text("transaction_id IS NOT NULL"),
        postgresql_where=sa.text("transaction_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_property_transaction_payment", table_name="property_payments")
    op.drop_index("uq_property_listing_fee_payment", table_name="property_payments")
    op.drop_index("ix_property_payments_status_created", table_name="property_payments")
    op.drop_index("ix_property_payments_payer_status", table_name="property_payments")
    op.drop_table("property_payments")
    op.drop_index("ix_property_transactions_seller_status", table_name="property_transactions")
    op.drop_index("ix_property_transactions_buyer_status", table_name="property_transactions")
    op.drop_table("property_transactions")
    op.drop_index("ix_property_listings_seller_created", table_name="property_listings")
    op.drop_index("ix_property_listings_public_search", table_name="property_listings")
    op.drop_table("property_listings")
    op.drop_index("ix_user_phone_verifications_phone", table_name="user_phone_verifications")
    op.drop_table("user_phone_verifications")
