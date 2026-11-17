"""Authenticated, fail-closed API for human-approved GitHub draft PR creation."""
from __future__ import annotations

import secrets
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai.change_proposals import ProposedFile, ProposalValidationError, validate_proposal
from app.ai.evidence_attestation import SandboxAttestation, SignedAttestation
from app.ai.governed_repository import (
    RepositoryWriteDenied,
    create_approved_draft_pull_request_with_app,
)
from app.ai.github_app_tokens import GitHubAppAuthError, GitHubAppTokenProvider
from app.ai.github_repository_writer import RepositoryWriteError
from app.ai.human_approval import HumanApproval, sign_human_approval
from app.ai.nonce_store import PostgresNonceStore
from app.api.deps import get_current_user, require_membership
from app.core.config import settings, _parse_github_repository_allowlist
from app.db import get_db
from app.models import AuditEvent, Membership, User

router = APIRouter(tags=["ai-governed-repository"])
_ACTION = "repository.pull_request.create"


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedFileInput(StrictInput):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=65_536)
    mode: Literal["100644"] = "100644"


class SandboxAttestationInput(StrictInput):
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    run_id: str = Field(min_length=1, max_length=128)
    sandbox_id: str = Field(min_length=1, max_length=128)
    tests_passed: bool = Field(strict=True)
    security_scan_passed: bool = Field(strict=True)
    regression_suite_passed: bool = Field(strict=True)
    issued_at: int = Field(ge=0)
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    signature_hex: str = Field(pattern=r"^[0-9a-f]{64}$")


class CreateApprovedRepositoryPRRequest(StrictInput):
    repository: str = Field(min_length=3, max_length=201)
    title: str = Field(min_length=5, max_length=120)
    summary: str = Field(min_length=1, max_length=8_000)
    files: list[ProposedFileInput] = Field(min_length=1, max_length=20)
    attestation: SandboxAttestationInput


class ApprovedRepositoryPRResponse(BaseModel):
    repository: str
    number: int
    url: str
    branch: str
    base_branch: str
    base_sha: str
    commit_sha: str
    artifact_sha256: str
    paths: tuple[str, ...]
    review_mode: Literal["draft_only"] = "draft_only"


class _CommitBeforeTokenProvider:
    """Commit consumed one-time proofs before any external GitHub side effect."""

    def __init__(self, provider: GitHubAppTokenProvider, db: Session) -> None:
        self._provider = provider
        self._db = db
        self.allowed_repositories = provider.allowed_repositories

    def get_token(self, repository: str) -> str:
        self._db.commit()
        return self._provider.get_token(repository)


@router.post(
    "/organizations/{organization_id}/ai/repository/draft-pr",
    response_model=ApprovedRepositoryPRResponse,
    status_code=201,
)
def create_human_approved_draft_pr(
    organization_id: str,
    payload: CreateApprovedRepositoryPRRequest,
    membership: Annotated[Membership, Depends(require_membership)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ApprovedRepositoryPRResponse:
    """Authenticate an owner/admin, issue a short-lived approval, then open a draft PR.

    The actor ID always comes from the authenticated account. No client-supplied
    actor ID, approval signature, or approval boolean is trusted by this endpoint.
    """
    if membership.organization_id != organization_id or membership.user_id != user.id:
        raise HTTPException(status_code=404, detail="Organization not found")
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Owner or admin role required")
    if not user.is_active or not user.email_verified or not user.mfa_enabled:
        raise HTTPException(
            status_code=403,
            detail="An active, email-verified account with MFA is required for repository approval",
        )
    if not settings.ai_repository_writes_enabled:
        raise HTTPException(status_code=503, detail="Governed repository writes are disabled")
    if settings.environment.strip().lower() not in {"staging", "production"}:
        raise HTTPException(status_code=503, detail="Governed repository writes require a trusted runtime")
    if not settings.database_url.lower().startswith(("postgresql://", "postgresql+")):
        raise HTTPException(status_code=503, detail="PostgreSQL nonce storage is required")

    try:
        allowed_repositories = _parse_github_repository_allowlist(
            settings.github_repository_allowlist
        )
        if payload.repository not in allowed_repositories:
            raise HTTPException(status_code=403, detail="Repository is not allowlisted")

        proposal = validate_proposal(
            ProposedFile(path=item.path, content=item.content, mode=item.mode)
            for item in payload.files
        )
        attestation_payload = SandboxAttestation(
            artifact_sha256=payload.attestation.artifact_sha256,
            run_id=payload.attestation.run_id,
            sandbox_id=payload.attestation.sandbox_id,
            tests_passed=payload.attestation.tests_passed,
            security_scan_passed=payload.attestation.security_scan_passed,
            regression_suite_passed=payload.attestation.regression_suite_passed,
            issued_at=payload.attestation.issued_at,
            nonce=payload.attestation.nonce,
            repository=payload.attestation.repository,
            base_sha=payload.attestation.base_sha,
        )
        signed_attestation = SignedAttestation(
            payload=attestation_payload,
            signature_hex=payload.attestation.signature_hex,
        )
        if proposal.sha256 != attestation_payload.artifact_sha256:
            raise HTTPException(status_code=422, detail="Sandbox evidence does not match the proposal")

        now = int(time.time())
        approval = HumanApproval(
            action_sha256=proposal.sha256,
            actor_id=user.id,
            approval_id=str(uuid4()),
            issued_at=now,
            expires_at=now + 300,
            nonce=secrets.token_urlsafe(32),
            action=_ACTION,
        )
        approval_key = settings.ai_human_approval_hmac_key.get_secret_value().encode("utf-8")
        signed_approval = sign_human_approval(approval, approval_key)
        attestation_key = settings.ai_sandbox_attestation_hmac_key.get_secret_value().encode("utf-8")

        nonce_store = PostgresNonceStore(db)
        audit_event = AuditEvent(
            actor_user_id=user.id,
            organization_id=organization_id,
            action="ai.repository.draft_pr.authorized",
            entity_type="repository_proposal",
            entity_id=proposal.sha256,
            details={
                "repository": payload.repository,
                "proposal_sha256": proposal.sha256,
                "sandbox_run_id": attestation_payload.run_id,
                "approval_id": approval.approval_id,
                "status": "authorized_pending_github_write",
            },
        )
        # This record is committed together with the one-time proof nonces
        # immediately before the first GitHub side effect.
        db.add(audit_event)
        provider = GitHubAppTokenProvider(
            app_id=settings.github_app_id,
            installation_id=settings.github_app_installation_id,
            private_key_pem=settings.github_app_private_key_pem.get_secret_value(),
            allowed_repositories=allowed_repositories,
        )
        try:
            result = create_approved_draft_pull_request_with_app(
                proposal,
                token_provider=_CommitBeforeTokenProvider(provider, db),
                repository=payload.repository,
                title=payload.title,
                summary=payload.summary,
                signed_attestation=signed_attestation,
                attestation_key=attestation_key,
                consume_attestation_nonce=nonce_store.consume_nonce,
                signed_approval=signed_approval,
                approval_key=approval_key,
                allowed_actor_ids={user.id},
                consume_approval_nonce=nonce_store.consume_nonce,
                now=now,
            )
        finally:
            provider.close()
        audit_event.details = {
            **audit_event.details,
            "status": "draft_pr_created",
            "pull_request_number": result.number,
            "pull_request_url": result.url,
            "branch": result.branch,
            "commit_sha": result.commit_sha,
        }
        db.add(audit_event)
        try:
            db.commit()
        except Exception:
            # The authorization event was already committed with the nonces.
            # Do not turn a completed external PR creation into a retryable 500.
            db.rollback()
        return ApprovedRepositoryPRResponse(
            repository=result.repository,
            number=result.number,
            url=result.url,
            branch=result.branch,
            base_branch=result.base_branch,
            base_sha=result.base_sha,
            commit_sha=result.commit_sha,
            artifact_sha256=result.artifact_sha256,
            paths=result.paths,
        )
    except HTTPException:
        db.rollback()
        raise
    except ProposalValidationError:
        db.rollback()
        raise HTTPException(status_code=422, detail="Repository proposal failed validation") from None
    except RepositoryWriteDenied:
        db.rollback()
        raise HTTPException(status_code=403, detail="Sandbox evidence or human approval was rejected") from None
    except (GitHubAppAuthError, RepositoryWriteError):
        db.rollback()
        if "audit_event" in locals():
            try:
                audit_event.details = {
                    **audit_event.details,
                    "status": "github_write_failed",
                }
                db.add(audit_event)
                db.commit()
            except Exception:
                db.rollback()
        raise HTTPException(status_code=502, detail="Governed GitHub draft PR operation failed") from None
    except (AttributeError, RuntimeError, ValueError):
        db.rollback()
        raise HTTPException(status_code=503, detail="Governed repository runtime is not configured safely") from None
