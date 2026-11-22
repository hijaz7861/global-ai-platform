"""Add role-gated education profiles, courses, enrollments and guardian consent."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0041_education_marketplace"
down_revision: Union[str, None] = "0040_property_marketplace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "education_settings",
        sa.Column("id", sa.Integer(), primary_key=True, server_default="1"),
        sa.Column("course_commission_bps", sa.Integer(), nullable=True),
        sa.Column(
            "updated_by_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_education_settings_singleton"),
        sa.CheckConstraint(
            "course_commission_bps IS NULL OR course_commission_bps BETWEEN 0 AND 10000",
            name="ck_education_settings_commission_bps",
        ),
    )
    op.execute("INSERT INTO education_settings (id, course_commission_bps) VALUES (1, NULL)")

    op.create_table(
        "education_student_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("preferred_language", sa.String(12), nullable=False, server_default="ur"),
        sa.Column("age_band", sa.String(24), nullable=False),
        sa.Column("age_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("age_verification_reference", sa.String(160), nullable=True),
        sa.Column(
            "age_verified_by_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("age_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="PK"),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_education_student_user"),
        sa.CheckConstraint(
            "age_band IN ('under_18','adult','prefer_not_to_say')",
            name="ck_education_student_age_band",
        ),
        sa.CheckConstraint("length(country_code) = 2", name="ck_education_student_country_code"),
        sa.CheckConstraint(
            "age_verified = false OR age_band = 'adult'",
            name="ck_education_student_age_verified_adult",
        ),
    )
    op.create_index(
        "ix_education_student_age_country",
        "education_student_profiles", ["age_band", "country_code"],
    )

    op.create_table(
        "education_guardian_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "student_profile_id", sa.String(36),
            sa.ForeignKey("education_student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("submission_reference", sa.String(160), nullable=True),
        sa.Column("verification_reference", sa.String(160), nullable=True),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(500), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("student_profile_id", name="uq_education_guardian_student"),
        sa.CheckConstraint(
            "status IN ('pending','verified','rejected')",
            name="ck_education_guardian_status",
        ),
    )
    op.create_index(
        "ix_education_guardian_status_created",
        "education_guardian_consents", ["status", "created_at"],
    )

    op.create_table(
        "education_teacher_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("subjects", sa.JSON(), nullable=False),
        sa.Column("qualifications", sa.String(500), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="PK"),
        sa.Column("online_classes", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verification_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("verification_reference", sa.String(160), nullable=True),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(500), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_education_teacher_user"),
        sa.CheckConstraint(
            "verification_status IN ('pending','verified','rejected','suspended')",
            name="ck_education_teacher_status",
        ),
        sa.CheckConstraint("length(country_code) = 2", name="ck_education_teacher_country_code"),
    )
    op.create_index(
        "ix_education_teacher_status_country",
        "education_teacher_profiles", ["verification_status", "country_code"],
    )

    op.create_table(
        "education_courses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "teacher_profile_id", sa.String(36),
            sa.ForeignKey("education_teacher_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("description", sa.String(3000), nullable=False, server_default=""),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("language", sa.String(12), nullable=False, server_default="ur"),
        sa.Column("age_group", sa.String(16), nullable=False, server_default="all"),
        sa.Column("course_type", sa.String(16), nullable=False, server_default="recorded"),
        sa.Column("price_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("commission_bps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="PK"),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(500), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "age_group IN ('under_18','adult','all')",
            name="ck_education_course_age_group",
        ),
        sa.CheckConstraint(
            "course_type IN ('recorded','live','notes','hybrid')",
            name="ck_education_course_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','published','rejected','suspended')",
            name="ck_education_course_status",
        ),
        sa.CheckConstraint(
            "price_minor >= 0 AND price_minor <= 9000000000000000",
            name="ck_education_course_price",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_education_course_currency_pkr"),
        sa.CheckConstraint(
            "commission_bps BETWEEN 0 AND 10000",
            name="ck_education_course_commission_bps",
        ),
    )
    op.create_index(
        "ix_education_course_discovery",
        "education_courses", ["status", "country_code", "city", "subject", "created_at"],
    )
    op.create_index(
        "ix_education_course_teacher_created",
        "education_courses", ["teacher_profile_id", "created_at"],
    )

    op.create_table(
        "education_course_enrollments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "course_id", sa.String(36),
            sa.ForeignKey("education_courses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "student_profile_id", sa.String(36),
            sa.ForeignKey("education_student_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("course_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("commission_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("teacher_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("payment_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("payment_method", sa.String(24), nullable=True),
        sa.Column("payment_reference", sa.String(160), nullable=True),
        sa.Column("payment_verification_reference", sa.String(160), nullable=True),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewer_notes", sa.String(500), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "course_id", "student_profile_id",
            name="uq_education_enrollment_course_student",
        ),
        sa.CheckConstraint(
            "payment_status IN ('pending','submitted','verified','rejected','refunded')",
            name="ck_education_enrollment_payment_status",
        ),
        sa.CheckConstraint(
            "course_price_minor >= 0 AND commission_minor >= 0 AND teacher_amount_minor >= 0",
            name="ck_education_enrollment_amounts_nonnegative",
        ),
        sa.CheckConstraint(
            "commission_minor + teacher_amount_minor = course_price_minor",
            name="ck_education_enrollment_amounts_balanced",
        ),
        sa.CheckConstraint("currency = 'PKR'", name="ck_education_enrollment_currency_pkr"),
        sa.CheckConstraint(
            "payment_method IS NULL OR payment_method IN ('bank_transfer','cash','manual')",
            name="ck_education_enrollment_payment_method",
        ),
    )
    op.create_index(
        "ix_education_enrollment_student_created",
        "education_course_enrollments", ["student_profile_id", "created_at"],
    )
    op.create_index(
        "ix_education_enrollment_payment_status",
        "education_course_enrollments", ["payment_status", "created_at"],
    )

    op.create_table(
        "education_course_materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "course_id", sa.String(36),
            sa.ForeignKey("education_courses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "teacher_profile_id", sa.String(36),
            sa.ForeignKey("education_teacher_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("material_type", sa.String(16), nullable=False),
        sa.Column("content_text", sa.String(12000), nullable=True),
        sa.Column("resource_url", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "reviewer_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "material_type IN ('notes','video_link','resource_link')",
            name="ck_education_material_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','published','rejected')",
            name="ck_education_material_status",
        ),
        sa.CheckConstraint(
            "resource_url IS NULL OR resource_url LIKE 'https://%'",
            name="ck_education_material_https_url",
        ),
    )
    op.create_index(
        "ix_education_material_course_status",
        "education_course_materials", ["course_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_education_material_course_status", table_name="education_course_materials")
    op.drop_table("education_course_materials")
    op.drop_index("ix_education_enrollment_payment_status", table_name="education_course_enrollments")
    op.drop_index("ix_education_enrollment_student_created", table_name="education_course_enrollments")
    op.drop_table("education_course_enrollments")
    op.drop_index("ix_education_course_teacher_created", table_name="education_courses")
    op.drop_index("ix_education_course_discovery", table_name="education_courses")
    op.drop_table("education_courses")
    op.drop_index("ix_education_teacher_status_country", table_name="education_teacher_profiles")
    op.drop_table("education_teacher_profiles")
    op.drop_index("ix_education_guardian_status_created", table_name="education_guardian_consents")
    op.drop_table("education_guardian_consents")
    op.drop_index("ix_education_student_age_country", table_name="education_student_profiles")
    op.drop_table("education_student_profiles")
    op.drop_table("education_settings")
