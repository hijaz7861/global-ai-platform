import hashlib
import json
from dataclasses import replace

import pytest

from app.ai.change_proposals import (
    ProposalValidationError,
    ProposedFile,
    ValidatedProposal,
    materialize_proposal,
    validate_proposal,
)


def test_materialize_verified_proposal_into_private_workspace(tmp_path):
    proposal = validate_proposal([
        ProposedFile("app/ai/example.py", "VALUE = 7\n"),
        ProposedFile("docs/guide.md", "safe text"),
    ])
    workspace = tmp_path / "candidate"
    workspace.mkdir()

    written = materialize_proposal(proposal, str(workspace))

    assert written == ("app/ai/example.py", "docs/guide.md")
    assert (workspace / "app/ai/example.py").read_text() == "VALUE = 7\n"
    assert (workspace / "docs/guide.md").read_text() == "safe text"


def test_materialize_rejects_tampered_artifact_digest(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    tampered = replace(proposal, artifact=proposal.artifact + b" ")

    with pytest.raises(ProposalValidationError, match="proposal_digest_mismatch"):
        materialize_proposal(tampered, str(workspace))


def test_materialize_requires_empty_workspace(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    (workspace / "existing.txt").write_text("do not overwrite")

    with pytest.raises(ProposalValidationError, match="workspace_must_be_empty"):
        materialize_proposal(proposal, str(workspace))


def test_materialize_rejects_workspace_symlink(tmp_path):
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    real_workspace = tmp_path / "real"
    real_workspace.mkdir()
    linked_workspace = tmp_path / "linked"
    linked_workspace.symlink_to(real_workspace, target_is_directory=True)

    with pytest.raises(ProposalValidationError, match="workspace_must_be_existing_real_directory"):
        materialize_proposal(proposal, str(linked_workspace))


def test_materialize_rejects_forged_traversal_even_with_matching_digest(tmp_path):
    artifact = json.dumps(
        {
            "schema_version": 1,
            "files": [{"path": "../outside.txt", "mode": "100644", "content": "escape"}],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    proposal = ValidatedProposal(
        artifact=artifact,
        sha256=hashlib.sha256(artifact).hexdigest(),
        paths=("../outside.txt",),
        risk_level="low",
        total_bytes=6,
    )
    workspace = tmp_path / "candidate"
    workspace.mkdir()

    with pytest.raises(ProposalValidationError, match="unsafe_path"):
        materialize_proposal(proposal, str(workspace))
