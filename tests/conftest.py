"""Shared test compatibility settings; security behavior has dedicated tests."""
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def disable_email_verification_for_legacy_domain_tests(monkeypatch):
    # Legacy domain tests focus on tenancy/commerce. Dedicated auth tests turn
    # this back on and exercise the real verification gate.
    monkeypatch.setattr(settings, "require_email_verification", False)
