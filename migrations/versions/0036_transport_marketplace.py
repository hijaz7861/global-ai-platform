"""Add organization-scoped transport profiles and moderated vehicle listings."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0036_transport_marketplace"
down_revision: Union[str, None] = "0035_role_entitlements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transport_provider_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36),
                  sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("business_name", sa.String(length=160), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state_region", sa.String(length=100), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default="PK"),
        sa.Column("service_description", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("review_notes", sa.String(length=1000), nullable=True),
        sa.Column("reviewed_by_user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", name="uq_transport_provider_profiles_org"),
        sa.UniqueConstraint("id", "organization_id", name="uq_transport_provider_profiles_id_org"),
        sa.CheckConstraint("status IN ('draft','submitted','approved','rejected','suspended')",
                           name="ck_transport_provider_profiles_status"),
        sa.CheckConstraint("length(country_code) = 2 AND country_code = upper(country_code)",
                           name="ck_transport_provider_profiles_country"),
    )
    op.create_index("ix_transport_provider_profiles_status_city",
                    "transport_provider_profiles", ["status", "country_code", "city"])
    op.create_table(
        "transport_vehicles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36),
                  sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider_profile_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vehicle_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("make_model", sa.String(length=100), nullable=True),
        sa.Column("capacity_description", sa.String(length=160), nullable=True),
        sa.Column("operator_included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state_region", sa.String(length=100), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default="PK"),
        sa.Column("pricing_basis", sa.String(length=16), nullable=False),
        sa.Column("rate_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="PKR"),
        sa.Column("minimum_charge_minor", sa.BigInteger(), nullable=True),
        sa.Column("service_radius_km", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("description", sa.String(length=1500), nullable=False, server_default=""),
        sa.Column("availability", sa.String(length=16), nullable=False, server_default="available"),
        sa.Column("verification_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("review_notes", sa.String(length=1000), nullable=True),
        sa.Column("reviewed_by_user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["provider_profile_id", "organization_id"],
            ["transport_provider_profiles.id", "transport_provider_profiles.organization_id"],
            ondelete="RESTRICT", name="fk_transport_vehicles_profile_org"),
        sa.CheckConstraint(
            "vehicle_type IN ('loader_rickshaw','qingqi','tractor_trolley','tractor_bucket',"
            "'dumper','truck','mazda','pickup','crane','jcb','excavator','backhoe_loader',"
            "'forklift','water_tanker','concrete_mixer','other')",
            name="ck_transport_vehicles_type"),
        sa.CheckConstraint("pricing_basis IN ('per_trip','per_hour','per_day','per_km','fixed_job')",
                           name="ck_transport_vehicles_pricing_basis"),
        sa.CheckConstraint("rate_minor >= 0", name="ck_transport_vehicles_rate_nonnegative"),
        sa.CheckConstraint("minimum_charge_minor IS NULL OR minimum_charge_minor >= 0",
                           name="ck_transport_vehicles_minimum_nonnegative"),
        sa.CheckConstraint("length(currency) = 3 AND currency = upper(currency)",
                           name="ck_transport_vehicles_currency"),
        sa.CheckConstraint("length(country_code) = 2 AND country_code = upper(country_code)",
                           name="ck_transport_vehicles_country"),
        sa.CheckConstraint("service_radius_km BETWEEN 1 AND 2000",
                           name="ck_transport_vehicles_service_radius"),
        sa.CheckConstraint("availability IN ('available','busy','offline')",
                           name="ck_transport_vehicles_availability"),
        sa.CheckConstraint("verification_status IN ('pending','verified','rejected','suspended')",
                           name="ck_transport_vehicles_verification"),
    )
    op.create_index("ix_transport_vehicles_discovery", "transport_vehicles",
                    ["verification_status", "availability", "vehicle_type", "country_code", "city"])
    op.create_index("ix_transport_vehicles_org_created", "transport_vehicles",
                    ["organization_id", "created_at"])
    op.create_index("ix_transport_vehicles_profile", "transport_vehicles", ["provider_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_transport_vehicles_profile", table_name="transport_vehicles")
    op.drop_index("ix_transport_vehicles_org_created", table_name="transport_vehicles")
    op.drop_index("ix_transport_vehicles_discovery", table_name="transport_vehicles")
    op.drop_table("transport_vehicles")
    op.drop_index("ix_transport_provider_profiles_status_city",
                  table_name="transport_provider_profiles")
    op.drop_table("transport_provider_profiles")
