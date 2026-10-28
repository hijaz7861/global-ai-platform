from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai.nonce_store import ai_evidence_nonces, make_durable_nonce_consumer


def test_nonce_is_single_use_and_only_hash_is_persisted():
    engine = create_engine("sqlite:///:memory:")
    ai_evidence_nonces.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = [datetime(2026, 10, 28, tzinfo=timezone.utc)]
    consume = make_durable_nonce_consumer(factory, clock=lambda: now[0])

    nonce = "nonce_0123456789"
    assert consume(nonce) is True
    assert consume(nonce) is False

    with factory() as session:
        rows = session.execute(select(ai_evidence_nonces)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["nonce_hash"] != nonce
    assert len(rows[0]["nonce_hash"]) == 64
    engine.dispose()


def test_expired_nonce_is_cleaned_and_can_be_used_again():
    engine = create_engine("sqlite:///:memory:")
    ai_evidence_nonces.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = [datetime(2026, 10, 28, tzinfo=timezone.utc)]
    consume = make_durable_nonce_consumer(factory, ttl_seconds=60, clock=lambda: now[0])

    nonce = "nonce_0123456789"
    assert consume(nonce) is True
    now[0] += timedelta(seconds=61)
    assert consume(nonce) is True
    engine.dispose()


def test_invalid_nonce_is_rejected_without_storage():
    engine = create_engine("sqlite:///:memory:")
    ai_evidence_nonces.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    consume = make_durable_nonce_consumer(factory)
    assert consume("short") is False
    engine.dispose()


def test_nonce_ttl_is_bounded():
    engine = create_engine("sqlite:///:memory:")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    import pytest

    with pytest.raises(ValueError, match="Nonce TTL"):
        make_durable_nonce_consumer(factory, ttl_seconds=10)
    engine.dispose()
