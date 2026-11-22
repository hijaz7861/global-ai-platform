"""Add workforce hiring requests and manual service-fee review."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0043_workforce_service_requests"
down_revision: Union[str, None] = "0042_workforce_directory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workforce_service_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_code", sa.String(40), nullable=False),
        sa.Column(
            "client_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "provider_profile_id", sa.String(36),
            sa.ForeignKey("workforce_profiles.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("service_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("details", sa.String(3000), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="PK"),
        sa.Column("budget_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("client_phone_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_terms_version", sa.String(48), nullable=False),
        sa.Column("client_terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_terms_version", sa.String(48), nullable=True),
        sa.Column("provider_terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("platform_fee_minor", sa.BigInteger(), nullable=False, server_default="5000"),
        sa.Column("platform_fee_currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("platform_fee_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("request_code", name="uq_workforce_service_request_code"),
        sa.CheckConstraint(
            "service_type IN ('hire_worker','hire_contractor')",
            name="ck_workforce_service_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('new','matched','accepted','in_progress','completed','cancelled','disputed')",
            name="ck_workforce_service_request_status",
        ),
        sa.CheckConstraint(
            "platform_fee_status IN ('pending','submitted','verified','rejected')",
            name="ck_workforce_service_request_fee_status",
        ),
        sa.CheckConstraint(
            "platform_fee_minor = 5000 AND platform_fee_currency = 'PKR'",
            name="ck_workforce_service_request_fee_snapshot",
        ),
        sa.CheckConstraint(
            "budget_minor IS NULL OR budget_minor BETWEEN 0 AND 9000000000000000",
            name="ck_workforce_service_request_budget",
        ),
        sa.CheckConstraint(
            "length(country_code) = 2",
            name="ck_workforce_service_request_country_code",
        ),
        sa.CheckConstraint(
            "(provider_terms_version IS NULL AND provider_terms_accepted_at IS NULL) OR "
            "(provider_terms_version IS NOT NULL AND provider_terms_accepted_at IS NOT NULL)",
            name="ck_workforce_service_request_provider_terms_pair",
        ),
    )
    op.create_index(
        "ix_workforce_service_request_client_created",
        "workforce_service_requests",
        ["client_user_id", "created_at"],
    )
    op.create_index(
        "ix_workforce_service_request_provider_status",
        "workforce_service_requests",
        ["provider_profile_id", "status", "platform_fee_status"],
    )
    op.create_index(
        "ix_workforce_service_request_fee_created",
        "workforce_service_requests",
        ["platform_fee_status", "created_at"],
    )

    op.create_table(
        "workforce_service_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "service_request_id", sa.String(36),
            sa.ForeignKey("workforce_service_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
        sa.Column("payment_method", sa.String(24), nullable=False),
        sa.Column("payment_reference", sa.String(160), nullable=False),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('submitted','verified','rejected')",
            name="ck_workforce_service_payment_status",
        ),
        sa.CheckConstraint(
            "payment_method IN ('bank_transfer','cash_deposit')",
            name="ck_workforce_service_payment_method",
        ),
        sa.CheckConstraint(
            "amount_minor = 5000 AND currency = 'PKR'",
            name="ck_workforce_service_payment_amount",
        ),
    )
    op.create_index(
        "uq_workforce_service_payment_active",
        "workforce_service_payments",
        ["service_request_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('submitted','verified')"),
        postgresql_where=sa.text("status IN ('submitted','verified')"),
    )
    op.create_index(
        "ix_workforce_service_payment_request_created",
        "workforce_service_payments",
        ["service_request_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workforce_service_payment_request_created",
        table_name="workforce_service_payments",
    )
    op.drop_index(
        "uq_workforce_service_payment_active",
        table_name="workforce_service_payments",
    )
    op.drop_table("workforce_service_payments")
    op.drop_index(
        "ix_workforce_service_request_fee_created",
        table_name="workforce_service_requests",
    )
    op.drop_index(
        "ix_workforce_service_request_provider_status",
        table_name="workforce_service_requests",
    )
    op.drop_index(
        "ix_workforce_service_request_client_created",
        table_name="workforce_service_requests",
    )
    op.drop_table("workforce_service_requests")
