"""Role-gated education marketplace with explicit minor-consent and payment review."""
from datetime import timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_active_role
from app.audit import record_audit_event
from app.core.config import settings
from app.db import get_db
from app.models import (
    EducationCourse,
    EducationCourseEnrollment,
    EducationCourseMaterial,
    EducationGuardianConsent,
    EducationSettings,
    EducationStudentProfile,
    EducationTeacherProfile,
    RoleDefinition,
    User,
    UserRoleEntitlement,
    utc_now,
)

router = APIRouter(tags=["education"])
MAX_MINOR_UNITS = 9_000_000_000_000_000


def _require_platform_admin(user: User) -> None:
    allowed = {
        item.strip().lower()
        for item in settings.platform_admin_emails.split(",")
        if item.strip()
    }
    if not user.email_verified or user.email.strip().lower() not in allowed:
        raise HTTPException(status_code=403, detail="Verified platform administrator required")


def _active_role_for_user(db: Session, user_id: str, role_key: str) -> bool:
    user = db.get(User, user_id)
    role = db.get(RoleDefinition, role_key)
    entitlement = db.scalar(
        select(UserRoleEntitlement)
        .where(
            UserRoleEntitlement.user_id == user_id,
            UserRoleEntitlement.role_key == role_key,
            UserRoleEntitlement.organization_id.is_(None),
            UserRoleEntitlement.status == "active",
        )
        .order_by(UserRoleEntitlement.activated_at.desc())
    )
    if user is None or not user.is_active or not user.email_verified or role is None or not role.is_active:
        return False
    if entitlement is None:
        return False
    if entitlement.fee_amount_minor > 0 and entitlement.payment_status != "verified":
        return False
    if entitlement.fee_amount_minor == 0 and entitlement.payment_status not in {"not_required", "verified"}:
        return False
    if role_key == "teacher" and not entitlement.age_verified:
        return False
    expiry = entitlement.expires_at
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= utc_now():
            return False
    return True


def _settings_row(db: Session) -> EducationSettings:
    item = db.get(EducationSettings, 1)
    if item is None:
        item = EducationSettings(id=1, course_commission_bps=None)
        db.add(item)
        db.flush()
    return item


def _audit(db: Session, user: User, action: str, entity_type: str, entity_id: str, state: str) -> None:
    record_audit_event(
        db,
        actor_user_id=user.id,
        organization_id=None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details={"status": state},
    )


def _student_payload(db: Session, item: EducationStudentProfile) -> dict:
    consent = db.scalar(
        select(EducationGuardianConsent).where(
            EducationGuardianConsent.student_profile_id == item.id
        )
    )
    return {
        "id": item.id,
        "display_name": item.display_name,
        "preferred_language": item.preferred_language,
        "age_band": item.age_band,
        "age_verified": item.age_verified,
        "guardian_consent_status": consent.status if consent else "not_submitted",
        "country_code": item.country_code,
        "city": item.city,
    }


def _teacher_payload(item: EducationTeacherProfile) -> dict:
    return {
        "id": item.id,
        "full_name": item.full_name,
        "subjects": item.subjects,
        "qualifications": item.qualifications,
        "bio": item.bio,
        "city": item.city,
        "country_code": item.country_code,
        "online_classes": item.online_classes,
        "verification_status": item.verification_status,
    }


def _course_payload(item: EducationCourse, teacher_name: str | None = None) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "subject": item.subject,
        "language": item.language,
        "age_group": item.age_group,
        "course_type": item.course_type,
        "price_minor": item.price_minor,
        "currency": item.currency,
        "commission_bps": item.commission_bps,
        "status": item.status,
        "teacher_name": teacher_name,
        "city": item.city,
        "country_code": item.country_code,
        "created_at": item.created_at,
    }


def _enrollment_payload(item: EducationCourseEnrollment) -> dict:
    return {
        "id": item.id,
        "course_id": item.course_id,
        "course_price_minor": item.course_price_minor,
        "commission_minor": item.commission_minor,
        "teacher_amount_minor": item.teacher_amount_minor,
        "currency": item.currency,
        "payment_status": item.payment_status,
        "created_at": item.created_at,
    }


def _require_student_course_age(db: Session, student: EducationStudentProfile, course: EducationCourse) -> None:
    if student.age_band == "under_18":
        consent = db.scalar(
            select(EducationGuardianConsent).where(
                EducationGuardianConsent.student_profile_id == student.id,
                EducationGuardianConsent.status == "verified",
            )
        )
        if consent is None:
            raise HTTPException(status_code=403, detail="Verified guardian consent is required for a minor")
        if course.age_group == "adult":
            raise HTTPException(status_code=403, detail="This course is restricted to verified adults")
    elif student.age_band == "adult":
        if course.age_group == "adult" and not student.age_verified:
            raise HTTPException(status_code=403, detail="Administrator-verified adult age is required")
    else:
        raise HTTPException(status_code=403, detail="Student age band must be reviewed before enrollment")


class StudentProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    preferred_language: str = Field(default="ur", min_length=2, max_length=12)
    age_band: Literal["under_18", "adult", "prefer_not_to_say"]
    country_code: str = Field(default="PK", min_length=2, max_length=2)
    city: str | None = Field(default=None, max_length=100)

    @field_validator("display_name", "preferred_language", "city")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        value = value.strip().upper()
        if not value.isascii() or not value.isalpha() or len(value) != 2:
            raise ValueError("Country code must be two ASCII letters")
        return value


class GuardianConsentRequest(BaseModel):
    submission_reference: str = Field(min_length=3, max_length=160)

    @field_validator("submission_reference")
    @classmethod
    def trim_reference(cls, value: str) -> str:
        return value.strip()


class TeacherProfileRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    subjects: list[str] = Field(min_length=1, max_length=20)
    qualifications: str | None = Field(default=None, max_length=500)
    bio: str | None = Field(default=None, max_length=2000)
    city: str | None = Field(default=None, max_length=100)
    country_code: str = Field(default="PK", min_length=2, max_length=2)
    online_classes: bool = True

    @field_validator("full_name", "qualifications", "bio", "city")
    @classmethod
    def trim_optional_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("subjects")
    @classmethod
    def normalize_subjects(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in values if item.strip()))
        if not cleaned:
            raise ValueError("At least one subject is required")
        return cleaned

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        value = value.strip().upper()
        if not value.isascii() or not value.isalpha() or len(value) != 2:
            raise ValueError("Country code must be two ASCII letters")
        return value


class CourseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=3000)
    subject: str = Field(min_length=1, max_length=100)
    language: str = Field(default="ur", min_length=2, max_length=12)
    age_group: Literal["under_18", "adult", "all"] = "all"
    course_type: Literal["recorded", "live", "notes", "hybrid"] = "recorded"
    price_minor: int = Field(default=0, ge=0, le=MAX_MINOR_UNITS)
    currency: Literal["PKR"] = "PKR"

    @field_validator("title", "description", "subject", "language")
    @classmethod
    def trim_course_text(cls, value: str) -> str:
        return value.strip()


class EnrollmentCreateRequest(BaseModel):
    payment_method: Literal["bank_transfer", "cash", "manual"] | None = None
    payment_reference: str | None = Field(default=None, max_length=160)

    @field_validator("payment_reference")
    @classmethod
    def trim_payment_reference(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class MaterialCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    material_type: Literal["notes", "video_link", "resource_link"] = "notes"
    content_text: str | None = Field(default=None, max_length=12000)
    resource_url: str | None = Field(default=None, max_length=1000)

    @field_validator("title", "content_text")
    @classmethod
    def trim_material_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("resource_url")
    @classmethod
    def require_https_resource(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("Learning resource URLs must use HTTPS")
        return value


class VerificationReviewRequest(BaseModel):
    decision: Literal["verify", "reject", "suspend"]
    evidence_checked: bool = False
    verification_reference: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("verification_reference", "notes")
    @classmethod
    def trim_review_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class ModerationReviewRequest(BaseModel):
    decision: Literal["publish", "reject", "suspend"]
    evidence_checked: bool = False
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class EducationSettingsRequest(BaseModel):
    course_commission_bps: int | None = Field(ge=0, le=10000)


class PaymentReviewRequest(BaseModel):
    decision: Literal["verify", "reject", "refund"]
    evidence_checked: bool = False
    verification_reference: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("verification_reference", "notes")
    @classmethod
    def trim_payment_review_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


@router.get("/education/courses")
def list_public_education_courses(
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    city: str | None = Query(default=None, max_length=100),
    subject: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[dict]:
    statement = (
        select(EducationCourse, EducationTeacherProfile.full_name)
        .join(EducationTeacherProfile, EducationTeacherProfile.id == EducationCourse.teacher_profile_id)
        .where(
            EducationCourse.status == "published",
            EducationTeacherProfile.verification_status == "verified",
        )
    )
    if country_code:
        statement = statement.where(EducationCourse.country_code == country_code.strip().upper())
    if city:
        statement = statement.where(EducationCourse.city == city.strip())
    if subject:
        statement = statement.where(EducationCourse.subject == subject.strip())
    rows = db.execute(statement.order_by(EducationCourse.created_at.desc()).limit(limit)).all()
    return [_course_payload(course, teacher_name) for course, teacher_name in rows]


@router.get("/me/education")
def get_my_education(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    student = db.scalar(select(EducationStudentProfile).where(EducationStudentProfile.user_id == user.id))
    teacher = db.scalar(select(EducationTeacherProfile).where(EducationTeacherProfile.user_id == user.id))
    courses = []
    enrollments = []
    if teacher is not None:
        courses = [
            _course_payload(item, teacher.full_name)
            for item in db.scalars(
                select(EducationCourse).where(EducationCourse.teacher_profile_id == teacher.id)
                .order_by(EducationCourse.created_at.desc()).limit(100)
            ).all()
        ]
    if student is not None:
        enrollments = [
            _enrollment_payload(item)
            for item in db.scalars(
                select(EducationCourseEnrollment).where(
                    EducationCourseEnrollment.student_profile_id == student.id
                ).order_by(EducationCourseEnrollment.created_at.desc()).limit(100)
            ).all()
        ]
    return {
        "student": _student_payload(db, student) if student else None,
        "teacher": _teacher_payload(teacher) if teacher else None,
        "courses": courses,
        "enrollments": enrollments,
    }


@router.put("/me/education/student-profile")
def save_student_profile(
    payload: StudentProfileRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("student"))],
) -> dict:
    item = db.scalar(
        select(EducationStudentProfile)
        .where(EducationStudentProfile.user_id == user.id)
        .with_for_update()
    )
    if item is None:
        item = EducationStudentProfile(
            user_id=user.id,
            display_name=payload.display_name,
            preferred_language=payload.preferred_language,
            age_band=payload.age_band,
            country_code=payload.country_code,
            city=payload.city,
        )
        db.add(item)
    else:
        age_changed = item.age_band != payload.age_band
        item.display_name = payload.display_name
        item.preferred_language = payload.preferred_language
        item.age_band = payload.age_band
        item.country_code = payload.country_code
        item.city = payload.city
        if age_changed:
            item.age_verified = False
            item.age_verification_reference = None
            item.age_verified_by_user_id = None
            item.age_verified_at = None
            consent = db.scalar(
                select(EducationGuardianConsent)
                .where(EducationGuardianConsent.student_profile_id == item.id)
                .with_for_update()
            )
            if consent is not None:
                consent.status = "pending"
                consent.submission_reference = None
                consent.verification_reference = None
                consent.reviewer_user_id = None
                consent.reviewer_notes = None
                consent.verified_at = None
                consent.updated_at = utc_now()
        item.updated_at = utc_now()
    db.flush()
    _audit(db, user, "education.student_profile.saved", "education_student_profile", item.id, "saved")
    db.commit()
    db.refresh(item)
    return {"student": _student_payload(db, item)}


@router.post("/me/education/guardian-consent", status_code=status.HTTP_201_CREATED)
def submit_guardian_consent(
    payload: GuardianConsentRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("student"))],
) -> dict:
    student = db.scalar(
        select(EducationStudentProfile).where(EducationStudentProfile.user_id == user.id)
    )
    if student is None or student.age_band != "under_18":
        raise HTTPException(status_code=409, detail="A minor student profile is required")
    consent = db.scalar(
        select(EducationGuardianConsent)
        .where(EducationGuardianConsent.student_profile_id == student.id)
        .with_for_update()
    )
    if consent is None:
        consent = EducationGuardianConsent(
            student_profile_id=student.id,
            status="pending",
            submission_reference=payload.submission_reference,
        )
        db.add(consent)
    elif consent.status == "verified":
        raise HTTPException(status_code=409, detail="Guardian consent is already verified")
    elif consent.status == "pending":
        raise HTTPException(status_code=409, detail="A guardian consent review is already pending")
    else:
        consent.status = "pending"
        consent.submission_reference = payload.submission_reference
        consent.verification_reference = None
        consent.reviewer_user_id = None
        consent.reviewer_notes = None
        consent.verified_at = None
        consent.updated_at = utc_now()
    db.flush()
    _audit(db, user, "education.guardian_consent.submitted", "education_guardian_consent", consent.id, "pending")
    db.commit()
    return {"guardian_consent": {"id": consent.id, "status": consent.status}}


@router.put("/me/education/teacher-profile")
def save_teacher_profile(
    payload: TeacherProfileRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("teacher"))],
) -> dict:
    item = db.scalar(
        select(EducationTeacherProfile)
        .where(EducationTeacherProfile.user_id == user.id)
        .with_for_update()
    )
    if item is None:
        item = EducationTeacherProfile(
            user_id=user.id,
            full_name=payload.full_name,
            subjects=payload.subjects,
            qualifications=payload.qualifications,
            bio=payload.bio,
            city=payload.city,
            country_code=payload.country_code,
            online_classes=payload.online_classes,
            verification_status="pending",
        )
        db.add(item)
    else:
        item.full_name = payload.full_name
        item.subjects = payload.subjects
        item.qualifications = payload.qualifications
        item.bio = payload.bio
        item.city = payload.city
        item.country_code = payload.country_code
        item.online_classes = payload.online_classes
        item.verification_status = "pending"
        item.reviewer_user_id = None
        item.reviewer_notes = None
        item.reviewed_at = None
        item.updated_at = utc_now()
    db.flush()
    _audit(db, user, "education.teacher_profile.saved", "education_teacher_profile", item.id, "pending")
    db.commit()
    db.refresh(item)
    return {"teacher": _teacher_payload(item)}


@router.post("/me/education/courses", status_code=status.HTTP_201_CREATED)
def create_education_course(
    payload: CourseCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("teacher"))],
) -> dict:
    teacher = db.scalar(
        select(EducationTeacherProfile).where(
            EducationTeacherProfile.user_id == user.id,
            EducationTeacherProfile.verification_status == "verified",
        )
    )
    if teacher is None or not _active_role_for_user(db, user.id, "teacher"):
        raise HTTPException(status_code=403, detail="A verified teacher profile and active teacher role are required")
    education_settings = _settings_row(db)
    if payload.price_minor > 0 and education_settings.course_commission_bps is None:
        raise HTTPException(status_code=409, detail="Paid courses are disabled until an administrator configures commission")
    commission_bps = education_settings.course_commission_bps or 0
    item = EducationCourse(
        teacher_profile_id=teacher.id,
        title=payload.title,
        description=payload.description,
        subject=payload.subject,
        language=payload.language,
        age_group=payload.age_group,
        course_type=payload.course_type,
        price_minor=payload.price_minor,
        currency="PKR",
        commission_bps=commission_bps,
        country_code=teacher.country_code,
        city=teacher.city,
        status="pending",
    )
    db.add(item)
    db.flush()
    _audit(db, user, "education.course.created", "education_course", item.id, "pending")
    db.commit()
    db.refresh(item)
    return {"course": _course_payload(item, teacher.full_name)}


@router.post("/me/education/courses/{course_id}/materials", status_code=status.HTTP_201_CREATED)
def create_course_material(
    course_id: str,
    payload: MaterialCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("teacher"))],
) -> dict:
    teacher = db.scalar(
        select(EducationTeacherProfile).where(
            EducationTeacherProfile.user_id == user.id,
            EducationTeacherProfile.verification_status == "verified",
        )
    )
    if teacher is None or not _active_role_for_user(db, user.id, "teacher"):
        raise HTTPException(status_code=403, detail="A verified teacher profile and active teacher role are required")
    course = db.scalar(
        select(EducationCourse).where(
            EducationCourse.id == course_id,
            EducationCourse.teacher_profile_id == teacher.id,
            EducationCourse.status.in_(["pending", "published"]),
        )
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found for this teacher")
    if payload.material_type in {"video_link", "resource_link"} and not payload.resource_url:
        raise HTTPException(status_code=422, detail="A HTTPS resource URL is required for link materials")
    item = EducationCourseMaterial(
        course_id=course.id,
        teacher_profile_id=teacher.id,
        title=payload.title,
        material_type=payload.material_type,
        content_text=payload.content_text,
        resource_url=payload.resource_url,
        status="pending",
    )
    db.add(item)
    db.flush()
    _audit(db, user, "education.material.created", "education_course_material", item.id, "pending")
    db.commit()
    return {"material": {"id": item.id, "title": item.title, "status": item.status}}


@router.post("/me/education/courses/{course_id}/enrollments", status_code=status.HTTP_201_CREATED)
def enroll_in_course(
    course_id: str,
    payload: EnrollmentCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("student"))],
) -> dict:
    student = db.scalar(
        select(EducationStudentProfile).where(EducationStudentProfile.user_id == user.id)
    )
    if student is None:
        raise HTTPException(status_code=409, detail="Create a student profile before enrollment")
    course = db.scalar(
        select(EducationCourse).where(
            EducationCourse.id == course_id,
            EducationCourse.status == "published",
        )
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Published course not found")
    _require_student_course_age(db, student, course)
    if course.price_minor == 0:
        if payload.payment_method or payload.payment_reference:
            raise HTTPException(status_code=422, detail="Free courses do not accept payment details")
        payment_status = "verified"
    else:
        if bool(payload.payment_method) != bool(payload.payment_reference):
            raise HTTPException(status_code=422, detail="Provide both a manual payment method and reference, or neither")
        payment_status = "submitted" if payload.payment_reference else "pending"
    commission_minor = (
        (course.price_minor * course.commission_bps + 5000) // 10000
        if course.price_minor > 0 else 0
    )
    item = EducationCourseEnrollment(
        course_id=course.id,
        student_profile_id=student.id,
        course_price_minor=course.price_minor,
        commission_minor=commission_minor,
        teacher_amount_minor=course.price_minor - commission_minor,
        currency="PKR",
        payment_status=payment_status,
        payment_method=payload.payment_method,
        payment_reference=payload.payment_reference,
    )
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Student is already enrolled in this course") from None
    _audit(db, user, "education.course.enrolled", "education_course_enrollment", item.id, payment_status)
    db.commit()
    db.refresh(item)
    return {"enrollment": _enrollment_payload(item)}


@router.get("/education/courses/{course_id}/materials")
def get_course_materials(
    course_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _role: Annotated[UserRoleEntitlement, Depends(require_active_role("student"))],
) -> list[dict]:
    student = db.scalar(
        select(EducationStudentProfile).where(EducationStudentProfile.user_id == user.id)
    )
    course = db.scalar(
        select(EducationCourse).where(
            EducationCourse.id == course_id,
            EducationCourse.status == "published",
        )
    )
    if student is None or course is None:
        raise HTTPException(status_code=404, detail="Student profile or published course not found")
    _require_student_course_age(db, student, course)
    if course.price_minor > 0:
        enrollment = db.scalar(
            select(EducationCourseEnrollment).where(
                EducationCourseEnrollment.course_id == course.id,
                EducationCourseEnrollment.student_profile_id == student.id,
                EducationCourseEnrollment.payment_status == "verified",
            )
        )
        if enrollment is None:
            raise HTTPException(status_code=403, detail="Verified course payment is required to access materials")
    rows = db.scalars(
        select(EducationCourseMaterial).where(
            EducationCourseMaterial.course_id == course.id,
            EducationCourseMaterial.status == "published",
        ).order_by(EducationCourseMaterial.created_at.asc()).limit(200)
    ).all()
    return [
        {
            "id": item.id,
            "title": item.title,
            "material_type": item.material_type,
            "content_text": item.content_text,
            "resource_url": item.resource_url,
        }
        for item in rows
    ]


@router.get("/platform/education/review-queue")
def list_education_review_queue(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    teachers = db.scalars(
        select(EducationTeacherProfile)
        .where(EducationTeacherProfile.verification_status == "pending")
        .order_by(EducationTeacherProfile.created_at.asc()).limit(100)
    ).all()
    consents = db.scalars(
        select(EducationGuardianConsent)
        .where(EducationGuardianConsent.status == "pending")
        .order_by(EducationGuardianConsent.created_at.asc()).limit(100)
    ).all()
    courses = db.scalars(
        select(EducationCourse).where(EducationCourse.status == "pending")
        .order_by(EducationCourse.created_at.asc()).limit(100)
    ).all()
    payments = db.scalars(
        select(EducationCourseEnrollment)
        .where(EducationCourseEnrollment.payment_status.in_(["pending", "submitted"]))
        .order_by(EducationCourseEnrollment.created_at.asc()).limit(100)
    ).all()
    materials = db.scalars(
        select(EducationCourseMaterial).where(EducationCourseMaterial.status == "pending")
        .order_by(EducationCourseMaterial.created_at.asc()).limit(100)
    ).all()
    return {
        "teachers": [{"id": item.id, "full_name": item.full_name, "status": item.verification_status} for item in teachers],
        "guardian_consents": [{"id": item.id, "student_profile_id": item.student_profile_id, "status": item.status} for item in consents],
        "courses": [{"id": item.id, "title": item.title, "status": item.status} for item in courses],
        "payments": [{"id": item.id, "course_id": item.course_id, "amount_minor": item.course_price_minor, "currency": item.currency, "status": item.payment_status} for item in payments],
        "materials": [{"id": item.id, "course_id": item.course_id, "title": item.title, "status": item.status} for item in materials],
    }


@router.patch("/platform/education/settings")
def update_education_settings(
    payload: EducationSettingsRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = _settings_row(db)
    item.course_commission_bps = payload.course_commission_bps
    item.updated_by_user_id = user.id
    item.updated_at = utc_now()
    _audit(
        db, user, "education.settings.updated", "education_settings", "1",
        "configured" if payload.course_commission_bps is not None else "unset",
    )
    db.commit()
    return {"course_commission_bps": item.course_commission_bps, "currency": "PKR"}


@router.post("/platform/education/teachers/{profile_id}/review")
def review_teacher_profile(
    profile_id: str,
    payload: VerificationReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(EducationTeacherProfile)
        .where(EducationTeacherProfile.id == profile_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Teacher profile not found")
    if payload.decision == "verify":
        if not payload.evidence_checked or not _active_role_for_user(db, item.user_id, "teacher"):
            raise HTTPException(status_code=409, detail="Reviewed evidence and an active paid, age-verified teacher role are required")
        item.verification_status = "verified"
    elif payload.decision == "suspend":
        item.verification_status = "suspended"
    else:
        item.verification_status = "rejected"
    item.reviewer_user_id = user.id
    item.reviewer_notes = payload.notes
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    _audit(db, user, "education.teacher.reviewed", "education_teacher_profile", item.id, item.verification_status)
    db.commit()
    return _teacher_payload(item)


@router.post("/platform/education/students/{profile_id}/age-review")
def review_student_age(
    profile_id: str,
    payload: VerificationReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(EducationStudentProfile)
        .where(EducationStudentProfile.id == profile_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Student profile not found")
    if item.age_band != "adult":
        raise HTTPException(status_code=409, detail="Only an adult-declared student profile can receive adult age review")
    if payload.decision == "verify":
        if not payload.evidence_checked or not payload.verification_reference:
            raise HTTPException(status_code=409, detail="Evidence check and verification reference are required")
        item.age_verified = True
        item.age_verification_reference = payload.verification_reference
        item.age_verified_by_user_id = user.id
        item.age_verified_at = utc_now()
    else:
        item.age_verified = False
        item.age_verification_reference = None
        item.age_verified_by_user_id = user.id
        item.age_verified_at = None
    item.updated_at = utc_now()
    _audit(db, user, "education.student_age.reviewed", "education_student_profile", item.id, "verified" if item.age_verified else "rejected")
    db.commit()
    return _student_payload(db, item)


@router.post("/platform/education/guardian-consents/{consent_id}/review")
def review_guardian_consent(
    consent_id: str,
    payload: VerificationReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(EducationGuardianConsent)
        .where(EducationGuardianConsent.id == consent_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Guardian consent record not found")
    student = db.get(EducationStudentProfile, item.student_profile_id)
    if student is None or student.age_band != "under_18":
        raise HTTPException(status_code=409, detail="Guardian consent is valid only for a minor student")
    if payload.decision == "verify":
        if not payload.evidence_checked or not payload.verification_reference:
            raise HTTPException(status_code=409, detail="Evidence check and verification reference are required")
        item.status = "verified"
        item.verification_reference = payload.verification_reference
        item.verified_at = utc_now()
    else:
        item.status = "rejected"
        item.verification_reference = None
        item.verified_at = None
    item.reviewer_user_id = user.id
    item.reviewer_notes = payload.notes
    item.updated_at = utc_now()
    _audit(db, user, "education.guardian_consent.reviewed", "education_guardian_consent", item.id, item.status)
    db.commit()
    return {"id": item.id, "status": item.status}


@router.post("/platform/education/courses/{course_id}/review")
def review_education_course(
    course_id: str,
    payload: ModerationReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(EducationCourse).where(EducationCourse.id == course_id).with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Education course not found")
    if payload.decision == "publish":
        teacher = db.get(EducationTeacherProfile, item.teacher_profile_id)
        if not payload.evidence_checked or teacher is None or teacher.verification_status != "verified":
            raise HTTPException(status_code=409, detail="Content review and verified teacher profile are required")
        if not _active_role_for_user(db, teacher.user_id, "teacher"):
            raise HTTPException(status_code=409, detail="An active teacher entitlement is required")
        item.status = "published"
    elif payload.decision == "suspend":
        item.status = "suspended"
    else:
        item.status = "rejected"
    item.reviewer_user_id = user.id
    item.reviewer_notes = payload.notes
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    _audit(db, user, "education.course.reviewed", "education_course", item.id, item.status)
    db.commit()
    return _course_payload(item)


@router.post("/platform/education/enrollments/{enrollment_id}/payment-review")
def review_education_payment(
    enrollment_id: str,
    payload: PaymentReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(EducationCourseEnrollment)
        .where(EducationCourseEnrollment.id == enrollment_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Course enrollment not found")
    if item.course_price_minor == 0:
        raise HTTPException(status_code=409, detail="Free course enrollment does not require payment review")
    if payload.decision == "verify":
        if item.payment_status not in {"pending", "submitted"}:
            raise HTTPException(status_code=409, detail="Only pending or submitted payments can be verified")
        if not payload.evidence_checked or not payload.verification_reference:
            raise HTTPException(status_code=409, detail="Evidence check and verification reference are required")
        item.payment_status = "verified"
        item.verified_at = utc_now()
    elif payload.decision == "refund":
        if item.payment_status != "verified":
            raise HTTPException(status_code=409, detail="Only verified course payments can be refunded")
        item.payment_status = "refunded"
        item.verified_at = None
    else:
        if item.payment_status not in {"pending", "submitted"}:
            raise HTTPException(status_code=409, detail="Only pending or submitted payments can be rejected")
        item.payment_status = "rejected"
        item.verified_at = None
    item.reviewer_user_id = user.id
    item.reviewer_notes = payload.notes
    item.updated_at = utc_now()
    _audit(db, user, "education.payment.reviewed", "education_course_enrollment", item.id, item.payment_status)
    db.commit()
    return _enrollment_payload(item)


@router.post("/platform/education/materials/{material_id}/review")
def review_education_material(
    material_id: str,
    payload: ModerationReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _require_platform_admin(user)
    item = db.scalar(
        select(EducationCourseMaterial)
        .where(EducationCourseMaterial.id == material_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Course material not found")
    if payload.decision == "publish":
        course = db.get(EducationCourse, item.course_id)
        teacher = db.get(EducationTeacherProfile, item.teacher_profile_id)
        if (
            not payload.evidence_checked or course is None or course.status != "published"
            or teacher is None or teacher.verification_status != "verified"
            or not _active_role_for_user(db, teacher.user_id, "teacher")
        ):
            raise HTTPException(status_code=409, detail="Published course and active verified teacher are required")
        item.status = "published"
    elif payload.decision == "suspend":
        item.status = "rejected"
    else:
        item.status = "rejected"
    item.reviewer_user_id = user.id
    item.reviewed_at = utc_now()
    item.updated_at = utc_now()
    _audit(db, user, "education.material.reviewed", "education_course_material", item.id, item.status)
    db.commit()
    return {"id": item.id, "status": item.status}
