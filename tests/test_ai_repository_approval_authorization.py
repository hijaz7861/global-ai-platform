"""Negative authorization tests for the governed repository approval API."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import ai_repository
from app.api.routes.ai_repository import (
    CreateApprovedRepositoryPRRequest,
    ProposedFileInput,
    SandboxAttestationInput,
)
from app.models import Membership, User


def _request() -> CreateApprovedRepositoryPRRequest:
    return CreateApprovedRepositoryPRRequest(
        repository="hijaz7861/global-ai-platform",
        title="Review governed runtime",
        summary="A bounded test proposal.",
        files=[ProposedFileInput(path="docs/review.md", content="review me")],
        attestation=SandboxAttestationInput(
            artifact_sha256="a" * 64,
            run_id="sandbox-run-authorization-01",
            sandbox_id="isolated-sandbox-01",
            tests_passed=True,
            security_scan_passed=True,
            regression_suite_passed=True,
            issued_at=1_800_000_000,
            nonce="sandbox-nonce-authorization-01",
            signature_hex="b" * 64,
        ),
    )


def _user(**overrides) -> User:
    values = {
        "id": "authenticated-owner-001",
        "email": "owner@example.test",
        "password_hash": "unused-test-hash",
        "is_active": True,
        "email_verified": True,
        "mfa_enabled": True,
    }
    values.update(overrides)
    return User(**values)


def _membership(user: User, **overrides) -> Membership:
    values = {
        "user_id": user.id,
        "organization_id": "org-authorization-001",
        "role": "owner",
    }
    values.update(overrides)
    return Membership(**values)


@pytest.mark.parametrize(
    ("user_overrides", "membership_overrides", "organization_id", "expected_status"),
    [
        ({}, {"role": "member"}, "org-authorization-001", 403),
        ({"email_verified": False}, {}, "org-authorization-001", 403),
        ({"mfa_enabled": False}, {}, "org-authorization-001", 403),
        ({"is_active": False}, {}, "org-authorization-001", 403),
        ({}, {}, "different-organization", 404),
    ],
)
def test_unauthorized_actor_never_constructs_github_provider(
    monkeypatch,
    user_overrides,
    membership_overrides,
    organization_id,
    expected_status,
):
    user = _user(**user_overrides)
    membership = _membership(user, **membership_overrides)
    db = SimpleNamespace(rollback=lambda: None)
    provider_constructions = []

    def forbidden_provider(**kwargs):
        provider_constructions.append(kwargs)
        raise AssertionError("GitHub provider must not be constructed before authorization")

    monkeypatch.setattr(ai_repository, "GitHubAppTokenProvider", forbidden_provider)

    with pytest.raises(HTTPException) as error:
        ai_repository.create_human_approved_draft_pr(
            organization_id, _request(), membership, user, db
        )

    assert error.value.status_code == expected_status
    assert provider_constructions == []


def test_disabled_repository_writer_fails_closed_before_provider_construction(monkeypatch):
    user = _user()
    membership = _membership(user)
    db = SimpleNamespace(rollback=lambda: None)
    provider_constructions = []

    monkeypatch.setattr(ai_repository.settings, "ai_repository_writes_enabled", False)
    monkeypatch.setattr(
        ai_repository,
        "GitHubAppTokenProvider",
        lambda **kwargs: provider_constructions.append(kwargs),
    )

    with pytest.raises(HTTPException) as error:
        ai_repository.create_human_approved_draft_pr(
            "org-authorization-001", _request(), membership, user, db
        )

    assert error.value.status_code == 503
    assert provider_constructions == []
