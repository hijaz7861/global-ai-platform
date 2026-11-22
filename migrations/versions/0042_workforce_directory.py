"""Add a privacy-minimized, role-gated workforce directory."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0042_workforce_directory"
down_revision: Union[str, None] = "0041_education_marketplace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workforce_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_key", sa.String(64),
            sa.ForeignKey("role_definitions.role_key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("specialty", sa.String(120), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("experience_years", sa.Numeric(4, 1), nullable=False, server_default="0.0"),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="PK"),
        sa.Column("service_radius_km", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("travels_for_work", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("available_cities", sa.JSON(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("availability", sa.String(16), nullable=False, server_default="available"),
        sa.Column("moderation_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(500), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "role_key", name="uq_workforce_user_role"),
        sa.CheckConstraint(
            "role_key IN ('contractor','mason','labour')",
            name="ck_workforce_role_key",
        ),
        sa.CheckConstraint(
            "experience_years >= 0 AND experience_years <= 70",
            name="ck_workforce_experience_range",
        ),
        sa.CheckConstraint(
            "service_radius_km BETWEEN 1 AND 1000",
            name="ck_workforce_service_radius",
        ),
        sa.CheckConstraint(
            "length(country_code) = 2",
            name="ck_workforce_country_code_length",
        ),
        sa.CheckConstraint(
            "availability IN ('available','unavailable')",
            name="ck_workforce_availability",
        ),
        sa.CheckConstraint(
            "moderation_status IN ('pending','published','rejected','suspended')",
            name="ck_workforce_moderation_status",
        ),
    )
    op.create_index(
        "ix_workforce_public_discovery",
        "workforce_profiles",
        ["role_key", "moderation_status", "availability", "country_code", "city"],
    )
    op.create_index(
        "ix_workforce_user_created",
        "workforce_profiles",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workforce_user_created", table_name="workforce_profiles")
    op.drop_index("ix_workforce_public_discovery", table_name="workforce_profiles")
    op.drop_table("workforce_profiles")
