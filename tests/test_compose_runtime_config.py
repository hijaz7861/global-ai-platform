"""Deployment configuration regression checks for the portable runtime."""
from pathlib import Path


def test_worker_receives_same_ai_budget_and_failover_controls_as_api():
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    api_section = compose.split("  api:\n", 1)[1].split("\n  worker:\n", 1)[0]
    worker_section = compose.split("  worker:\n", 1)[1].split("\nvolumes:\n", 1)[0]

    for variable in (
        "AI_AUTO_FAILOVER_ENABLED",
        "AI_DAILY_REQUEST_LIMIT",
        "AI_DAILY_TOKEN_LIMIT",
    ):
        assert f"{variable}:" in api_section
        assert f"{variable}:" in worker_section

    assert "AI_DAILY_TOKEN_LIMIT: ${AI_DAILY_TOKEN_LIMIT:-0}" in worker_section
    assert "AI_AUTO_FAILOVER_ENABLED: ${AI_AUTO_FAILOVER_ENABLED:-false}" in worker_section
