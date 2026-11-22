"""Education role, minor-consent, moderation and payment security regressions."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import EducationSettings, RoleDefinition


@pytest.fixture
def education_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "education-test-signing-key-not-for-production")
    monkeypatch.setattr(settings, "platform_admin_emails", "platform-admin@example.com")
    monkeypatch.setattr(settings, "require_email_verification", True)
    with sessions() as db:
        db.add_all([
            RoleDefinition(
                role_key="student", display_name="Student", description="Student role",
                min_age=0, fee_amount_minor=0, fee_currency="PKR", requires_review=True, is_active=True,
            ),
            RoleDefinition(
                role_key="teacher", display_name="Teacher", description="Teacher role",
                min_age=18, fee_amount_minor=20_000, fee_currency="PKR", requires_review=True, is_active=True,
            ),
        ])
        db.add(EducationSettings(id=1, course_commission_bps=None))
        db.commit()

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def activate_role(client, user_token, admin_headers, role_key):
    application = client.post(
        "/api/v1/me/role-entitlements",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"role_key": role_key},
    )
    assert application.status_code == 201, application.text
    review_payload = {"decision": "approve"}
    if role_key == "teacher":
        review_payload.update({"payment_verified": True, "age_verified": True})
    review = client.post(
        f"/api/v1/platform/role-entitlements/{application.json()['id']}/review",
        headers=admin_headers,
        json=review_payload,
    )
    assert review.status_code == 200, review.text
    return application.json()["id"]


def make_teacher(client, token, admin_headers, email_suffix):
    profile = client.put(
        "/api/v1/me/education/teacher-profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": f"Teacher {email_suffix}",
            "subjects": ["Mathematics", "Physics"],
            "qualifications": "Reviewed qualification summary",
            "city": "Lahore",
            "country_code": "PK",
        },
    )
    assert profile.status_code == 200, profile.text
    profile_id = profile.json()["teacher"]["id"]
    reviewed = client.post(
        f"/api/v1/platform/education/teachers/{profile_id}/review",
        headers=admin_headers,
        json={
            "decision": "verify",
            "evidence_checked": True,
            "verification_reference": f"TEACHER-REVIEW-{email_suffix}",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    return profile_id


def make_course(client, teacher_token, admin_headers, *, age_group="all", price_minor=0, title="Portable course"):
    created = client.post(
        "/api/v1/me/education/courses",
        headers={"Authorization": f"Bearer {teacher_token}"},
        json={
            "title": title,
            "description": "Course created in the portable platform.",
            "subject": "Mathematics",
            "language": "ur",
            "age_group": age_group,
            "course_type": "recorded",
            "price_minor": price_minor,
            "currency": "PKR",
        },
    )
    assert created.status_code == 201, created.text
    course_id = created.json()["course"]["id"]
    published = client.post(
        f"/api/v1/platform/education/courses/{course_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "evidence_checked": True, "notes": "Content reviewed"},
    )
    assert published.status_code == 200, published.text
    return course_id


def test_minor_enrollment_requires_separately_verified_guardian_consent(
    education_client, verified_user_factory
):
    client = education_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    teacher_token = verified_user_factory(client, "minor-course-teacher@example.com")
    activate_role(client, teacher_token, admin_headers, "teacher")
    make_teacher(client, teacher_token, admin_headers, "minor")
    free_course = make_course(client, teacher_token, admin_headers, title="All ages course")
    adult_course = make_course(
        client, teacher_token, admin_headers, age_group="adult", title="Adult course"
    )

    student_token = verified_user_factory(client, "minor-student@example.com")
    activate_role(client, student_token, admin_headers, "student")
    student_headers = {"Authorization": f"Bearer {student_token}"}

    self_asserted = client.put(
        "/api/v1/me/education/student-profile",
        headers=student_headers,
        json={
            "display_name": "Student",
            "age_band": "under_18",
            "country_code": "PK",
            "guardian_consent": True,
        },
    )
    assert self_asserted.status_code == 422

    profile = client.put(
        "/api/v1/me/education/student-profile",
        headers=student_headers,
        json={"display_name": "Student", "age_band": "under_18", "country_code": "PK"},
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["student"]["guardian_consent_status"] == "not_submitted"

    denied = client.post(
        f"/api/v1/me/education/courses/{free_course}/enrollments",
        headers=student_headers,
        json={},
    )
    assert denied.status_code == 403
    assert "guardian consent" in denied.json()["detail"].lower()

    consent = client.post(
        "/api/v1/me/education/guardian-consent",
        headers=student_headers,
        json={"submission_reference": "GUARDIAN-CASE-001"},
    )
    assert consent.status_code == 201, consent.text
    assert consent.json()["guardian_consent"]["status"] == "pending"

    queue = client.get("/api/v1/platform/education/review-queue", headers=admin_headers)
    assert queue.status_code == 200
    assert queue.json()["guardian_consents"][0]["submission_reference"] == "GUARDIAN-CASE-001"
    assert "email" not in queue.json()["guardian_consents"][0]

    self_review = client.post(
        f"/api/v1/platform/education/guardian-consents/{consent.json()['guardian_consent']['id']}/review",
        headers=student_headers,
        json={
            "decision": "verify",
            "evidence_checked": True,
            "verification_reference": "SELF-APPROVED",
        },
    )
    assert self_review.status_code == 403

    unverified_review = client.post(
        f"/api/v1/platform/education/guardian-consents/{consent.json()['guardian_consent']['id']}/review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": False},
    )
    assert unverified_review.status_code == 409

    verified_review = client.post(
        f"/api/v1/platform/education/guardian-consents/{consent.json()['guardian_consent']['id']}/review",
        headers=admin_headers,
        json={
            "decision": "verify",
            "evidence_checked": True,
            "verification_reference": "MANUAL-GUARDIAN-CHECK-001",
        },
    )
    assert verified_review.status_code == 200, verified_review.text
    assert verified_review.json()["status"] == "verified"

    enrolled = client.post(
        f"/api/v1/me/education/courses/{free_course}/enrollments",
        headers=student_headers,
        json={},
    )
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["enrollment"]["payment_status"] == "verified"

    adult_denied = client.post(
        f"/api/v1/me/education/courses/{adult_course}/enrollments",
        headers=student_headers,
        json={},
    )
    assert adult_denied.status_code == 403


def test_paid_course_requires_age_review_manual_payment_and_material_access_gate(
    education_client, verified_user_factory
):
    client = education_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    teacher_token = verified_user_factory(client, "paid-course-teacher@example.com")
    activate_role(client, teacher_token, admin_headers, "teacher")
    make_teacher(client, teacher_token, admin_headers, "paid")

    commission = client.patch(
        "/api/v1/platform/education/settings",
        headers=admin_headers,
        json={"course_commission_bps": 1000},
    )
    assert commission.status_code == 200, commission.text

    course_id = make_course(
        client, teacher_token, admin_headers,
        age_group="adult", price_minor=10_000, title="Paid adult course",
    )

    student_token = verified_user_factory(client, "paid-course-student@example.com")
    activate_role(client, student_token, admin_headers, "student")
    student_headers = {"Authorization": f"Bearer {student_token}"}
    student = client.put(
        "/api/v1/me/education/student-profile",
        headers=student_headers,
        json={"display_name": "Adult learner", "age_band": "adult", "country_code": "PK"},
    )
    assert student.status_code == 200, student.text
    student_id = student.json()["student"]["id"]

    age_denied = client.post(
        f"/api/v1/me/education/courses/{course_id}/enrollments",
        headers=student_headers,
        json={},
    )
    assert age_denied.status_code == 403

    age_review_denied = client.post(
        f"/api/v1/platform/education/students/{student_id}/age-review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": False},
    )
    assert age_review_denied.status_code == 409
    age_verified = client.post(
        f"/api/v1/platform/education/students/{student_id}/age-review",
        headers=admin_headers,
        json={
            "decision": "verify",
            "evidence_checked": True,
            "verification_reference": "MANUAL-AGE-CHECK-001",
        },
    )
    assert age_verified.status_code == 200, age_verified.text
    assert age_verified.json()["age_verified"] is True

    enrollment = client.post(
        f"/api/v1/me/education/courses/{course_id}/enrollments",
        headers=student_headers,
        json={"payment_method": "bank_transfer", "payment_reference": "BANK-REFERENCE-001"},
    )
    assert enrollment.status_code == 201, enrollment.text
    enrollment_payload = enrollment.json()["enrollment"]
    assert enrollment_payload["payment_status"] == "submitted"
    assert enrollment_payload["commission_minor"] == 1000
    assert enrollment_payload["teacher_amount_minor"] == 9000
    assert "payment_reference" not in enrollment_payload

    material = client.post(
        f"/api/v1/me/education/courses/{course_id}/materials",
        headers={"Authorization": f"Bearer {teacher_token}"},
        json={
            "title": "Lesson notes",
            "material_type": "notes",
            "content_text": "A short lesson.",
        },
    )
    assert material.status_code == 201, material.text
    material_id = material.json()["material"]["id"]
    material_review = client.post(
        f"/api/v1/platform/education/materials/{material_id}/review",
        headers=admin_headers,
        json={"decision": "publish", "evidence_checked": True},
    )
    assert material_review.status_code == 200, material_review.text

    locked_materials = client.get(
        f"/api/v1/education/courses/{course_id}/materials",
        headers=student_headers,
    )
    assert locked_materials.status_code == 403

    unverified_payment = client.post(
        f"/api/v1/platform/education/enrollments/{enrollment_payload['id']}/payment-review",
        headers=admin_headers,
        json={"decision": "verify", "evidence_checked": False},
    )
    assert unverified_payment.status_code == 409

    verified_payment = client.post(
        f"/api/v1/platform/education/enrollments/{enrollment_payload['id']}/payment-review",
        headers=admin_headers,
        json={
            "decision": "verify",
            "evidence_checked": True,
            "verification_reference": "MANUAL-BANK-RECONCILIATION-001",
        },
    )
    assert verified_payment.status_code == 200, verified_payment.text
    assert verified_payment.json()["payment_status"] == "verified"

    available_materials = client.get(
        f"/api/v1/education/courses/{course_id}/materials",
        headers=student_headers,
    )
    assert available_materials.status_code == 200, available_materials.text
    assert available_materials.json()[0]["title"] == "Lesson notes"

    duplicate = client.post(
        f"/api/v1/me/education/courses/{course_id}/enrollments",
        headers=student_headers,
        json={"payment_method": "bank_transfer", "payment_reference": "BANK-REFERENCE-002"},
    )
    assert duplicate.status_code == 409


def test_paid_course_fails_closed_without_commission_and_teacher_ownership_is_enforced(
    education_client, verified_user_factory
):
    client = education_client
    admin_token = verified_user_factory(client, "platform-admin@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    teacher_one = verified_user_factory(client, "teacher-one@example.com")
    teacher_one_entitlement = activate_role(client, teacher_one, admin_headers, "teacher")
    make_teacher(client, teacher_one, admin_headers, "one")
    paid_course = client.post(
        "/api/v1/me/education/courses",
        headers={"Authorization": f"Bearer {teacher_one}"},
        json={"title": "Blocked paid course", "subject": "Science", "price_minor": 1000},
    )
    assert paid_course.status_code == 409

    free_course = make_course(client, teacher_one, admin_headers, title="Teacher one course")
    teacher_two = verified_user_factory(client, "teacher-two@example.com")
    activate_role(client, teacher_two, admin_headers, "teacher")
    make_teacher(client, teacher_two, admin_headers, "two")
    wrong_owner = client.post(
        f"/api/v1/me/education/courses/{free_course}/materials",
        headers={"Authorization": f"Bearer {teacher_two}"},
        json={"title": "Not my course", "content_text": "No access"},
    )
    assert wrong_owner.status_code == 404

    public_before = client.get("/api/v1/education/courses")
    assert public_before.status_code == 200
    assert any(item["id"] == free_course for item in public_before.json())

    suspended = client.post(
        f"/api/v1/platform/role-entitlements/{teacher_one_entitlement}/lifecycle",
        headers=admin_headers,
        json={"action": "suspend"},
    )
    assert suspended.status_code == 200, suspended.text
    public_after = client.get("/api/v1/education/courses")
    assert public_after.status_code == 200
    assert all(item["id"] != free_course for item in public_after.json())
    blocked_create = client.post(
        "/api/v1/me/education/courses",
        headers={"Authorization": f"Bearer {teacher_one}"},
        json={"title": "Suspended teacher course", "subject": "Science"},
    )
    assert blocked_create.status_code == 403
