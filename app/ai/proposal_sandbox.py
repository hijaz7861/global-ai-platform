"""Connect verified proposal artifacts to the existing isolated Docker runner.

This orchestration wrapper only prepares a disposable candidate workspace and
invokes the runner. It does not sign evidence, consume approval nonces, or release code.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
import secrets
import tempfile
import time
from uuid import uuid4

from app.ai.change_proposals import ValidatedProposal, materialize_proposal
from app.ai.evidence_attestation import SandboxAttestation, SignedAttestation, sign_attestation
from app.ai.sandbox_runner import DockerSandboxRunner, SandboxResult


def run_proposal_in_sandbox(
    proposal: ValidatedProposal,
    *,
    runner: DockerSandboxRunner,
    trusted_test_directory: str | Path,
    command: list[str],
) -> SandboxResult:
    """Materialize a verified proposal and run checks in a disposable sandbox.

    The trusted test directory must come from a protected source independent of
    the proposal. Candidate files are deleted after the runner exits or raises.
    """
    with tempfile.TemporaryDirectory(prefix="gai-proposal-") as candidate_directory:
        materialize_proposal(proposal, candidate_directory)
        return runner.run(
            artifact_directory=candidate_directory,
            trusted_test_directory=trusted_test_directory,
            command=command,
        )


@dataclass(frozen=True)
class ProposalValidationSuiteResult:
    """Independent results from trusted test, security, and regression commands."""

    artifact_sha256: str
    tests: SandboxResult
    security_scan: SandboxResult
    regression: SandboxResult

    @property
    def passed(self) -> bool:
        return all(
            result.exit_code == 0
            and not result.timed_out
            and not result.output_limit_exceeded
            for result in (self.tests, self.security_scan, self.regression)
        )


def run_proposal_validation_suite(
    proposal: ValidatedProposal,
    *,
    runner: DockerSandboxRunner,
    trusted_test_directory: str | Path,
    test_command: list[str],
    security_command: list[str],
    regression_command: list[str],
    on_check_start: Callable[[], None] | None = None,
) -> ProposalValidationSuiteResult:
    """Run three separately configured checks against the same immutable candidate.

    Commands and trusted tests must be supplied by the trusted operator/runtime,
    never generated from proposal contents. Each check runs in a fresh container;
    the candidate workspace is read-only and removed after all checks complete.
    """
    commands = (test_command, security_command, regression_command)
    if any(not isinstance(command, list) or not command for command in commands):
        raise ValueError("All validation commands must be non-empty argument lists")
    with tempfile.TemporaryDirectory(prefix="gai-proposal-suite-") as candidate_directory:
        materialize_proposal(proposal, candidate_directory)
        results = []
        for command in commands:
            if on_check_start is not None:
                on_check_start()
            results.append(runner.run(
                artifact_directory=candidate_directory,
                trusted_test_directory=trusted_test_directory,
                command=command,
            ))
        return ProposalValidationSuiteResult(
            artifact_sha256=proposal.sha256,
            tests=results[0],
            security_scan=results[1],
            regression=results[2],
        )


def sign_validation_suite_result(
    result: ProposalValidationSuiteResult,
    *,
    attestation_key: bytes,
) -> SignedAttestation:
    """Sign successful validation evidence inside a trusted sandbox controller.

    Keep the HMAC key in a secret manager and never expose it to candidate code,
    model prompts, or untrusted task payloads. This helper does not authenticate a
    human approver or consume the attestation nonce; verification does that later.
    """
    if not isinstance(result, ProposalValidationSuiteResult) or not result.passed:
        raise ValueError("Only a fully passing validation suite can be attested")
    payload = SandboxAttestation(
        artifact_sha256=result.artifact_sha256,
        run_id=str(uuid4()),
        sandbox_id=str(uuid4()),
        tests_passed=True,
        security_scan_passed=True,
        regression_suite_passed=True,
        issued_at=int(time.time()),
        nonce=secrets.token_urlsafe(24),
    )
    return sign_attestation(payload, attestation_key)
