"""Connect verified proposal artifacts to the existing isolated Docker runner.

This orchestration wrapper only prepares a disposable candidate workspace and
invokes the runner. It does not sign evidence, consume approval nonces, or release code.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from app.ai.change_proposals import ValidatedProposal, materialize_proposal
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
