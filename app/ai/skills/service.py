"""Immutable, versioned skill content with explicit owner approval."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.models import AISkillVersion

_SKILL_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{1,63}$")
_LICENSE = re.compile(r"^[A-Za-z0-9.+_-]{2,64}$")
_DATA_CLASSES = {"public", "internal", "confidential", "restricted"}
_RISK_LEVELS = {"low", "medium", "high", "critical"}
_SOURCE_TYPES = {"internal", "user_authored", "imported"}


class SkillNotFound(LookupError):
    """Skill version is not visible in the caller's organization scope."""


class SkillConflict(ValueError):
    """A skill version already exists or its state changed concurrently."""


def create_skill_version(
    db: Session,
    *,
    organization_id: str,
    actor_user_id: str,
    skill_id: str,
    version: str,
    title: str,
    description: str,
    content: str,
    source_type: str,
    license_id: str,
    source_ref: str | None = None,
    required_capabilities: list[str] | None = None,
    data_classes: list[str] | None = None,
    risk_level: str = "low",
) -> AISkillVersion:
    normalized_content = content.strip()
    normalized_title = title.strip()
    normalized_description = description.strip()
    capabilities = required_capabilities or []
    classes = data_classes or []
    if not _SKILL_ID.fullmatch(skill_id) or not _VERSION.fullmatch(version):
        raise ValueError("skill_id or semantic version is invalid")
    if not normalized_title or len(normalized_title) > 160:
        raise ValueError("Skill title must contain 1-160 characters")
    if len(normalized_description) > 500:
        raise ValueError("Skill description must not exceed 500 characters")
    if not normalized_content or len(normalized_content.encode("utf-8")) > 50_000:
        raise ValueError("Skill content must be non-empty and no larger than 50000 UTF-8 bytes")
    if source_type not in _SOURCE_TYPES or not _LICENSE.fullmatch(license_id):
        raise ValueError("Skill source type or license identifier is invalid")
    if source_ref is not None and len(source_ref) > 256:
        raise ValueError("Skill source reference must not exceed 256 characters")
    if source_type == "imported" and not source_ref:
        raise ValueError("Imported skills require a source reference")
    if risk_level not in _RISK_LEVELS:
        raise ValueError("Invalid skill risk level")
    if len(capabilities) > 32 or len(set(capabilities)) != len(capabilities):
        raise ValueError("Skill capabilities must be unique and limited to 32 entries")
    if any(not _CAPABILITY.fullmatch(item) for item in capabilities):
        raise ValueError("Skill capability identifier is invalid")
    if len(classes) > 4 or len(set(classes)) != len(classes) or any(item not in _DATA_CLASSES for item in classes):
        raise ValueError("Skill data classes are invalid")

    record = AISkillVersion(
        organization_id=organization_id,
        skill_id=skill_id,
        version=version,
        title=normalized_title,
        description=normalized_description,
        content=normalized_content,
        content_sha256=sha256(normalized_content.encode("utf-8")).hexdigest(),
        source_type=source_type,
        source_ref=source_ref,
        license_id=license_id,
        required_capabilities=capabilities,
        data_classes=classes,
        risk_level=risk_level,
        status="draft",
        created_by_user_id=actor_user_id,
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise SkillConflict("This skill ID and version already exist in the organization") from None
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action="ai.skill.created",
        entity_type="ai_skill_version",
        entity_id=record.id,
        details={"status": "draft"},
    )
    db.commit()
    db.expire_all()
    db.refresh(record)
    return record


def _verify_content_digest(record: AISkillVersion) -> AISkillVersion:
    actual = sha256(record.content.encode("utf-8")).hexdigest()
    if actual != record.content_sha256:
        raise SkillConflict("Skill content digest does not match its stored manifest")
    return record


def list_skill_versions(
    db: Session, *, organization_id: str, role: str,
) -> list[AISkillVersion]:
    query = select(AISkillVersion).where(AISkillVersion.organization_id == organization_id)
    if role not in {"owner", "admin"}:
        query = query.where(AISkillVersion.status == "approved")
    records = db.scalars(
        query.order_by(AISkillVersion.skill_id, AISkillVersion.created_at.desc()).limit(200)
    ).all()
    return [_verify_content_digest(record) for record in records]


def get_skill_version(
    db: Session, *, organization_id: str, skill_id: str, version: str, role: str,
) -> AISkillVersion:
    query = select(AISkillVersion).where(
        AISkillVersion.organization_id == organization_id,
        AISkillVersion.skill_id == skill_id,
        AISkillVersion.version == version,
    )
    if role not in {"owner", "admin"}:
        query = query.where(AISkillVersion.status == "approved")
    record = db.scalar(query)
    if record is None:
        raise SkillNotFound("Skill version not found")
    return _verify_content_digest(record)


def approve_skill_version(
    db: Session, *, organization_id: str, skill_id: str, version: str, actor_user_id: str,
) -> AISkillVersion:
    record = db.scalar(select(AISkillVersion).where(
        AISkillVersion.organization_id == organization_id,
        AISkillVersion.skill_id == skill_id,
        AISkillVersion.version == version,
        AISkillVersion.status == "draft",
    ))
    if record is None:
        raise SkillNotFound("Draft skill version not found")
    actual_digest = sha256(record.content.encode("utf-8")).hexdigest()
    if actual_digest != record.content_sha256:
        raise SkillConflict("Skill content digest does not match its stored manifest")
    now = datetime.now(timezone.utc)
    changed = db.execute(update(AISkillVersion).where(
        AISkillVersion.id == record.id,
        AISkillVersion.status == "draft",
        AISkillVersion.content_sha256 == actual_digest,
    ).values(
        status="approved",
        approved_by_user_id=actor_user_id,
        approved_at=now,
        updated_at=now,
    ).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise SkillConflict("Skill version changed during approval")
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action="ai.skill.approved",
        entity_type="ai_skill_version",
        entity_id=record.id,
        details={"status": "approved"},
    )
    db.commit()
    db.expire_all()
    approved = db.scalar(select(AISkillVersion).where(AISkillVersion.id == record.id))
    assert approved is not None
    return approved


def disable_skill_version(
    db: Session, *, organization_id: str, skill_id: str, version: str, actor_user_id: str,
) -> None:
    record = db.scalar(select(AISkillVersion).where(
        AISkillVersion.organization_id == organization_id,
        AISkillVersion.skill_id == skill_id,
        AISkillVersion.version == version,
        AISkillVersion.status == "approved",
    ))
    if record is None:
        raise SkillNotFound("Approved skill version not found")
    now = datetime.now(timezone.utc)
    changed = db.execute(update(AISkillVersion).where(
        AISkillVersion.id == record.id,
        AISkillVersion.status == "approved",
    ).values(status="disabled", updated_at=now).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise SkillConflict("Skill version changed during disable")
    record_audit_event(
        db,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action="ai.skill.disabled",
        entity_type="ai_skill_version",
        entity_id=record.id,
        details={"status": "disabled"},
    )
    db.commit()
    db.expire_all()
