"""Add platform role catalogue and independent user role entitlements."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0035_role_entitlements"
down_revision: Union[str, None] = "0034_postgres_approval_nonces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "role_definitions",
        sa.Column("role_key", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("min_age", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("fee_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("fee_currency", sa.String(length=3), nullable=False, server_default="PKR"),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("min_age >= 0 AND min_age <= 120", name="ck_role_definitions_min_age"),
        sa.CheckConstraint("fee_amount_minor IS NULL OR fee_amount_minor >= 0", name="ck_role_definitions_fee_nonnegative"),
        sa.CheckConstraint("length(fee_currency) = 3", name="ck_role_definitions_currency"),
    )
    op.create_table(
        "user_role_entitlements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_key", sa.String(length=64), sa.ForeignKey("role_definitions.role_key", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
        sa.Column("payment_status", sa.String(length=24), nullable=False, server_default="not_required"),
        sa.Column("fee_amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fee_currency", sa.String(length=3), nullable=False, server_default="PKR"),
        sa.Column("payment_reference", sa.String(length=160), nullable=True),
        sa.Column("age_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewer_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewer_notes", sa.String(length=1000), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending_payment','pending_review','active','rejected','expired','suspended','revoked')", name="ck_user_role_entitlements_status"),
        sa.CheckConstraint("payment_status IN ('not_required','pending','verified','rejected')", name="ck_user_role_entitlements_payment_status"),
        sa.CheckConstraint("fee_amount_minor >= 0", name="ck_user_role_entitlements_fee_nonnegative"),
    )
    op.create_index(
        "uq_user_role_entitlement_global", "user_role_entitlements", ["user_id", "role_key"],
        unique=True, postgresql_where=sa.text("organization_id IS NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
        sqlite_where=sa.text("organization_id IS NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
    )
    op.create_index(
        "uq_user_role_entitlement_org", "user_role_entitlements", ["user_id", "role_key", "organization_id"],
        unique=True, postgresql_where=sa.text("organization_id IS NOT NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
        sqlite_where=sa.text("organization_id IS NOT NULL AND status IN ('pending_payment', 'pending_review', 'active', 'suspended')"),
    )
    op.create_index("ix_user_role_entitlements_user_status", "user_role_entitlements", ["user_id", "status"])
    op.create_index("ix_user_role_entitlements_role_status", "user_role_entitlements", ["role_key", "status"])
    op.create_table(
        "role_entitlement_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entitlement_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_role_entitlement_events_entitlement_created", "role_entitlement_events", ["entitlement_id", "created_at"])
    op.create_index("ix_role_entitlement_events_actor_created", "role_entitlement_events", ["actor_user_id", "created_at"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("""
            CREATE TRIGGER role_entitlement_events_no_update
            BEFORE UPDATE ON role_entitlement_events
            BEGIN
                SELECT RAISE(ABORT, 'role entitlement events are append-only');
            END
        """)
        op.execute("""
            CREATE TRIGGER role_entitlement_events_no_delete
            BEFORE DELETE ON role_entitlement_events
            BEGIN
                SELECT RAISE(ABORT, 'role entitlement events are append-only');
            END
        """)
    elif dialect == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_role_entitlement_event_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $
            BEGIN
                RAISE EXCEPTION 'role entitlement events are append-only';
            END;
            $
        """)
        op.execute("""
            CREATE TRIGGER role_entitlement_events_no_update_or_delete
            BEFORE UPDATE OR DELETE ON role_entitlement_events
            FOR EACH ROW EXECUTE FUNCTION prevent_role_entitlement_event_mutation()
        """)
    else:
        raise RuntimeError(f"Unsupported database dialect for role-event immutability: {dialect}")

    roles = sa.table(
        "role_definitions",
        sa.column("role_key", sa.String),
        sa.column("display_name", sa.String),
        sa.column("description", sa.String),
        sa.column("min_age", sa.Integer),
        sa.column("fee_amount_minor", sa.BigInteger),
        sa.column("fee_currency", sa.String),
        sa.column("requires_review", sa.Boolean),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(roles, [
        {"role_key": "student", "display_name": "Student", "description": "Student account; course purchases are separate.", "min_age": 0, "fee_amount_minor": 0, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "teacher", "display_name": "Teacher", "description": "Teacher profile; age and credentials require administrator verification.", "min_age": 18, "fee_amount_minor": 20000, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "contractor", "display_name": "Contractor", "description": "Professional contractor profile; identity and age review required.", "min_age": 18, "fee_amount_minor": 50000, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "mason", "display_name": "Mason", "description": "Skilled construction workforce profile.", "min_age": 18, "fee_amount_minor": 20000, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "labour", "display_name": "Labour", "description": "Construction workforce profile.", "min_age": 18, "fee_amount_minor": 5000, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "supplier", "display_name": "Supplier", "description": "Supplier onboarding; fee must be configured before applications open.", "min_age": 18, "fee_amount_minor": None, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "transporter", "display_name": "Transporter", "description": "Transport and machinery provider; fee must be configured before applications open.", "min_age": 18, "fee_amount_minor": None, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "property_agent", "display_name": "Property Agent", "description": "Property professional; fee must be configured before applications open.", "min_age": 18, "fee_amount_minor": None, "fee_currency": "PKR", "requires_review": True, "is_active": True},
        {"role_key": "construction_provider", "display_name": "Construction Service Provider", "description": "Construction services provider; fee must be configured before applications open.", "min_age": 18, "fee_amount_minor": None, "fee_currency": "PKR", "requires_review": True, "is_active": True},
    ])


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS role_entitlement_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS role_entitlement_events_no_delete")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS role_entitlement_events_no_update_or_delete ON role_entitlement_events")
        op.execute("DROP FUNCTION IF EXISTS prevent_role_entitlement_event_mutation()")
    op.drop_index("ix_role_entitlement_events_actor_created", table_name="role_entitlement_events")
    op.drop_index("ix_role_entitlement_events_entitlement_created", table_name="role_entitlement_events")
    op.drop_table("role_entitlement_events")
    op.drop_index("ix_user_role_entitlements_role_status", table_name="user_role_entitlements")
    op.drop_index("uq_user_role_entitlement_org", table_name="user_role_entitlements")
    op.drop_index("uq_user_role_entitlement_global", table_name="user_role_entitlements")
    op.drop_index("ix_user_role_entitlements_user_status", table_name="user_role_entitlements")
    op.drop_table("user_role_entitlements")
    op.drop_table("role_definitions")
