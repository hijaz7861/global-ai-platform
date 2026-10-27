"""Tests for the platform's safe workflow-engine tool contract."""
import pytest
from pydantic import ValidationError

from app.automation.contracts import validate_mcp_tool_call


def test_read_only_workflow_list_accepts_only_empty_arguments():
    assert validate_mcp_tool_call("workflow.list", {}) == {}


def test_workflow_reference_rejects_path_traversal():
    with pytest.raises(ValidationError):
        validate_mcp_tool_call("workflow.inspect", {"workflowFile": "workflows/../.env"})


def test_workflow_reference_must_be_a_json_file_in_workflows_directory():
    with pytest.raises(ValidationError):
        validate_mcp_tool_call("workflow.inspect", {"workflowFile": "/tmp/workflow.json"})


def test_proposal_accepts_valid_goal_and_normalizes_whitespace():
    result = validate_mcp_tool_call(
        "workflow.propose",
        {"workflowFile": "workflows/monthly-report.json", "goal": "  Prepare a report  "},
    )
    assert result == {
        "workflowFile": "workflows/monthly-report.json",
        "goal": "Prepare a report",
    }


def test_client_cannot_add_an_approval_flag_to_proposal():
    with pytest.raises(ValidationError):
        validate_mcp_tool_call(
            "workflow.propose",
            {
                "workflowFile": "workflows/monthly-report.json",
                "goal": "Prepare a report",
                "approval": True,
            },
        )


def test_workflow_execution_tool_is_not_allowlisted():
    with pytest.raises(ValueError, match="not permitted"):
        validate_mcp_tool_call(
            "workflow.execute",
            {"workflowFile": "workflows/monthly-report.json", "approval": True},
        )


def test_design_goal_is_bounded():
    with pytest.raises(ValidationError):
        validate_mcp_tool_call(
            "workflow.design",
            {"workflowFile": "workflows/monthly-report.json", "goal": "x" * 2001},
        )


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        validate_mcp_tool_call("workflow.list", {"organization_id": "tenant-a"})
