import hashlib

import pytest

from app.ai.change_proposals import (
    ProposalValidationError,
    ProposedFile,
    validate_proposal,
)

def test_proposal_is_canonical_and_order_independent():
    first = validate_proposal([
        ProposedFile("docs/B.md", "second"),
        ProposedFile("docs/A.md", "first"),
    ])
    second = validate_proposal([
        ProposedFile("docs/A.md", "first"),
        ProposedFile("docs/B.md", "second"),
    ])

    assert first.artifact == second.artifact
    assert first.sha256 == second.sha256 == hashlib.sha256(first.artifact).hexdigest()
    assert first.paths == ("docs/A.md", "docs/B.md")
    assert first.risk_level == "low"


def test_source_code_proposal_is_medium_risk():
    proposal = validate_proposal([ProposedFile("app/ai/new_tool.py", "VALUE = 1\n")])
    assert proposal.risk_level == "medium"


@pytest.mark.parametrize("path", [
    "../escape.py",
    "/absolute.py",
    "app\\windows.py",
    "app//empty.py",
    "app/../escape.py",
    ".github/workflows/ci.yml",
    "app/security/policy.py",
    "migrations/0001_init.py",
    ".env.production",
    "keys/private.pem",
    "pyproject.toml",
    "requirements.txt",
])
def test_unsafe_or_sensitive_paths_are_rejected(path):
    with pytest.raises(ProposalValidationError):
        validate_proposal([ProposedFile(path, "content")])


def test_duplicate_paths_are_case_insensitive():
    with pytest.raises(ProposalValidationError, match="duplicate_path"):
        validate_proposal([
            ProposedFile("docs/Guide.md", "one"),
            ProposedFile("docs/guide.md", "two"),
        ])


def test_binary_content_and_executable_modes_are_rejected():
    with pytest.raises(ProposalValidationError, match="binary_content_not_allowed"):
        validate_proposal([ProposedFile("docs/data.txt", "a\x00b")])
    with pytest.raises(ProposalValidationError, match="unsupported_file_mode"):
        validate_proposal([ProposedFile("app/run.py", "print(1)", mode="100755")])


def test_file_count_and_size_are_bounded():
    with pytest.raises(ProposalValidationError, match="too_many_files"):
        validate_proposal([ProposedFile(f"docs/{i}.md", "x") for i in range(21)])
    with pytest.raises(ProposalValidationError, match="file_too_large"):
        validate_proposal([ProposedFile("docs/large.md", "x" * 65_537)])


def test_proposal_cannot_be_empty():
    with pytest.raises(ProposalValidationError, match="empty_proposal"):
        validate_proposal([])
