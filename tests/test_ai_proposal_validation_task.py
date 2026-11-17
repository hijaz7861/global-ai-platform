from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.ai.evidence_attestation import SandboxAttestation, SignedAttestation
from app.ai.proposal_sandbox import ProposalValidationSuiteResult
from app.ai.sandbox_runner import SandboxResult
from app.ai.runtime.tasks import PermanentTaskError
from app.ai import proposal_validation_task as handler


@dataclass
class FakeMembership:
    role: str = "owner"


class FakeDB:
    def __init__(self, membership=None):
        self.membership = membership or FakeMembership()
        self.commits = 0

    def scalar(self, statement):
        return self.membership

    def commit(self):
        self.commits += 1


class FakeContext:
    def __init__(self, membership=None):
        self.db = FakeDB(membership)
        self.task = SimpleNamespace(
            id="task-proposal-0001",
            organization_id="org-0001",
            created_by_user_id="user-0001",
            input_payload={
                "files": [
                    {"path": "docs/worker-validation.md", "content": "Validated proposal\n"}
                ]
            },
        )
        self.heartbeats = 0

    def heartbeat(self):
        self.heartbeats += 1


def _suite(passed=True):
    ok = SandboxResult(0, "ignored", "", False, False, 0.1)
    bad = SandboxResult(1, "ignored", "ignored", False, False, 0.2)
    return ProposalValidationSuiteResult(
        artifact_sha256="a" * 64,
        tests=ok,
        security_scan=ok if passed else bad,
        regression=ok,
    )


def _signed():
    payload = SandboxAttestation(
        artifact_sha256="a" * 64,
        run_id="run-00000001",
        sandbox_id="sandbox-000001",
        tests_passed=True,
        security_scan_passed=True,
        regression_suite_passed=True,
        issued_at=1_800_000_000,
        nonce="nonce-abcdefghijklmnop",
    )
    return SignedAttestation(payload=payload, signature_hex="b" * 64)


def _configure(monkeypatch, suite):
    monkeypatch.setattr(
        handler,
        "_runtime_configuration",
        lambda: (object(), object(), {
            "unit": ["pytest", "unit"],
            "security": ["scanner"],
            "regression": ["pytest", "regression"],
        }, b"x" * 32),
    )
    monkeypatch.setattr(handler, "run_proposal_validation_suite", lambda *a, **kw: suite)
    monkeypatch.setattr(handler, "sign_validation_suite_result", lambda *a, **kw: _signed())
    audit = []
    monkeypatch.setattr(handler, "record_audit_event", lambda *a, **kw: audit.append(kw))
    return audit


def test_worker_validation_returns_signed_attestation_only_after_all_checks_pass(monkeypatch):
    context = FakeContext()
    audit = _configure(monkeypatch, _suite(passed=True))

    result = handler.run_proposal_validation_task(context)

    assert result["status"] == "passed"
    assert result["artifact_sha256"] == "a" * 64
    assert result["attestation"]["payload"]["run_id"] == "run-00000001"
    assert result["attestation"]["signature_hex"] == "b" * 64
    assert set(result["checks"]) == {"unit", "security", "regression"}
    assert context.heartbeats == 2
    assert context.db.commits == 1
    assert audit[0]["action"] == "ai.proposal.validation.completed"


def test_worker_validation_failure_never_returns_attestation(monkeypatch):
    context = FakeContext()
    audit = _configure(monkeypatch, _suite(passed=False))

    result = handler.run_proposal_validation_task(context)

    assert result["status"] == "failed"
    assert result["attestation"] is None
    assert result["checks"]["security"]["exit_code"] == 1
    assert context.db.commits == 1
    assert audit[0]["details"]["status"] == "failed"


def test_worker_validation_rejects_revoked_membership_before_sandbox(monkeypatch):
    context = FakeContext(membership=FakeMembership(role="member"))
    called = []
    monkeypatch.setattr(handler, "_runtime_configuration", lambda: called.append(True))

    with pytest.raises(PermanentTaskError, match="membership_revoked"):
        handler.run_proposal_validation_task(context)

    assert called == []


def test_worker_validation_rejects_unexpected_proposal_fields(monkeypatch):
    context = FakeContext()
    context.task.input_payload["files"][0]["execute"] = "rm -rf /"
    called = []
    monkeypatch.setattr(handler, "_runtime_configuration", lambda: called.append(True))

    with pytest.raises(PermanentTaskError, match="invalid_proposal_input"):
        handler.run_proposal_validation_task(context)

    assert called == []


def test_worker_validation_does_not_sign_failed_suite(monkeypatch):
    context = FakeContext()
    _configure(monkeypatch, _suite(passed=False))
    signed_calls = []
    monkeypatch.setattr(handler, "sign_validation_suite_result", lambda *a, **kw: signed_calls.append(True))

    result = handler.run_proposal_validation_task(context)

    assert result["attestation"] is None
    assert signed_calls == []
