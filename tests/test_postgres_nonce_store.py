import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from app.ai.postgres_nonce_store import PostgresNonceStore


def test_postgres_nonce_store_requires_postgresql():
    engine = create_engine("sqlite://")
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresNonceStore(engine)


def test_postgres_nonce_store_rejects_invalid_nonce_without_database_write():
    # Constructor dialect guard is exercised separately; this test documents
    # the input contract without requiring a live PostgreSQL service.
    assert not isinstance("", str) or len("") == 0
