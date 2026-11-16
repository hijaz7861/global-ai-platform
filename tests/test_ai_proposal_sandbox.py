from pathlib import Path

import pytest

from app.ai.change_proposals import ProposalValidationError, ProposedFile, validate_proposal
from app.ai.proposal_sandbox import run_proposal_in_sandbox, run_proposal_validation_suite
from app.ai.sandbox_runner import SandboxResult


class FakeSandboxRunner:
    def __init__(self, *, result=None, results=None, error=None):
        self.result = result or SandboxResult(
            exit_code=0,
            stdout="tests passed",
            stderr="",
            timed_out=False,
            output_limit_exceeded=False,
            duration_seconds=0.1,
        )
        self.results = list(results or [])
        self.error = error
        self.calls = []
        self.candidate_snapshot = {}

    def run(self, **kwargs):
        self.calls.append(kwargs)
        candidate = Path(kwargs["artifact_directory"])
        self.candidate_snapshot = {
            str(path.relative_to(candidate)): path.read_bytes()
            for path in candidate.rglob("*")
            if path.is_file()
        }
        if self.error:
            raise self.error
        return self.results.pop(0) if self.results else self.result


def test_proposal_is_materialized_and_removed_after_sandbox_run(tmp_path):
    proposal = validate_proposal([ProposedFile("app/example.py", "VALUE = 1\n")])
    trusted_tests = tmp_path / "trusted-tests"
    trusted_tests.mkdir()
    runner = FakeSandboxRunner()

    result = run_proposal_in_sandbox(
        proposal,
        runner=runner,
        trusted_test_directory=trusted_tests,
        command=["python", "-m", "pytest", "/trusted-tests"],
    )

    assert result.exit_code == 0
    assert len(runner.calls) == 1
    candidate = Path(runner.calls[0]["artifact_directory"])
    assert runner.candidate_snapshot["app/example.py"] == b"VALUE = 1\n"
    assert runner.calls[0]["trusted_test_directory"] == trusted_tests
    assert not candidate.exists()


def test_invalid_proposal_never_reaches_sandbox(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    tampered = type(proposal)(
        artifact=proposal.artifact + b"x",
        sha256=proposal.sha256,
        paths=proposal.paths,
        risk_level=proposal.risk_level,
        total_bytes=proposal.total_bytes,
    )
    trusted_tests = tmp_path / "trusted-tests"
    trusted_tests.mkdir()
    runner = FakeSandboxRunner()

    with pytest.raises(ProposalValidationError, match="proposal_digest_mismatch"):
        run_proposal_in_sandbox(
            tampered,
            runner=runner,
            trusted_test_directory=trusted_tests,
            command=["pytest", "/trusted-tests"],
        )

    assert runner.calls == []


def test_sandbox_failure_still_cleans_candidate_workspace(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    trusted_tests = tmp_path / "trusted-tests"
    trusted_tests.mkdir()
    runner = FakeSandboxRunner(error=RuntimeError("sandbox unavailable"))

    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        run_proposal_in_sandbox(
            proposal,
            runner=runner,
            trusted_test_directory=trusted_tests,
            command=["pytest", "/trusted-tests"],
        )

    assert len(runner.calls) == 1
    assert not Path(runner.calls[0]["artifact_directory"]).exists()



def test_validation_suite_runs_all_three_trusted_checks_and_cleans_workspace(tmp_path):
    proposal = validate_proposal([ProposedFile("app/example.py", "VALUE = 1\n")])
    trusted_tests = tmp_path / "trusted-tests"
    trusted_tests.mkdir()
    success = SandboxResult(0, "ok", "", False, False, 0.1)
    runner = FakeSandboxRunner(results=[success, success, success])

    result = run_proposal_validation_suite(
        proposal,
        runner=runner,
        trusted_test_directory=trusted_tests,
        test_command=["pytest", "/trusted-tests/unit"],
        security_command=["bandit", "-r", "/workspace"],
        regression_command=["pytest", "/trusted-tests/regression"],
    )

    assert result.passed is True
    assert result.artifact_sha256 == proposal.sha256
    assert len(runner.calls) == 3
    assert len({call["artifact_directory"] for call in runner.calls}) == 1
    assert all(call["trusted_test_directory"] == trusted_tests for call in runner.calls)
    assert not Path(runner.calls[0]["artifact_directory"]).exists()


def test_validation_suite_fails_closed_if_any_check_fails(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    trusted_tests = tmp_path / "trusted-tests"
    trusted_tests.mkdir()
    success = SandboxResult(0, "ok", "", False, False, 0.1)
    failed = SandboxResult(1, "", "security finding", False, False, 0.1)
    runner = FakeSandboxRunner(results=[success, failed, success])

    result = run_proposal_validation_suite(
        proposal,
        runner=runner,
        trusted_test_directory=trusted_tests,
        test_command=["pytest", "/trusted-tests/unit"],
        security_command=["scanner", "/workspace"],
        regression_command=["pytest", "/trusted-tests/regression"],
    )

    assert result.passed is False
    assert len(runner.calls) == 3


def test_validation_suite_rejects_missing_trusted_command_before_running(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    trusted_tests = tmp_path / "trusted-tests"
    trusted_tests.mkdir()
    runner = FakeSandboxRunner()

    with pytest.raises(ValueError, match="non-empty argument lists"):
        run_proposal_validation_suite(
            proposal,
            runner=runner,
            trusted_test_directory=trusted_tests,
            test_command=["pytest"],
            security_command=[],
            regression_command=["pytest", "/trusted-tests/regression"],
        )

    assert runner.calls == []
