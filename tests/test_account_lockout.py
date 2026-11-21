"""Persistent account lockout behavior tests."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import User, utc_now


def test_password_failures_lock_account_and_success_clears_expired_lock(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(settings, "require_email_verification", False)
    monkeypatch.setattr(settings, "auth_account_lockout_threshold", 3)
    monkeypatch.setattr(settings, "auth_account_lockout_minutes", 15)

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            password = "correct-horse-battery-12"
            registered = client.post(
                "/api/v1/auth/register",
                json={"email": "lockout@example.com", "password": password},
            )
            assert registered.status_code == 201, registered.text

            for _ in range(3):
                failed = client.post(
                    "/api/v1/auth/token",
                    json={"email": "lockout@example.com", "password": "incorrect-password"},
                )
                assert failed.status_code == 401

            locked = client.post(
                "/api/v1/auth/token",
                json={"email": "lockout@example.com", "password": password},
            )
            assert locked.status_code == 401

            db = sessions()
            try:
                user = db.scalar(select(User).where(User.email == "lockout@example.com"))
                assert user is not None
                assert user.failed_login_attempts == 3
                assert user.locked_until is not None
                user.locked_until = utc_now() - timedelta(seconds=1)
                db.commit()
            finally:
                db.close()

            recovered = client.post(
                "/api/v1/auth/token",
                json={"email": "lockout@example.com", "password": password},
            )
            assert recovered.status_code == 200, recovered.text

            db = sessions()
            try:
                user = db.scalar(select(User).where(User.email == "lockout@example.com"))
                assert user is not None
                assert user.failed_login_attempts == 0
                assert user.locked_until is None
            finally:
                db.close()
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
