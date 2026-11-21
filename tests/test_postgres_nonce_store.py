from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import dialect

from app.ai.postgres_nonce_store import PostgresNonceStore


def test_postgres_nonce_store_requires_postgresql():
    engine = create_engine("sqlite://")
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresNonceStore(engine)


def test_postgres_nonce_store_rejects_invalid_nonce_without_opening_transaction():
    engine = Mock()
    engine.dialect.name = "postgresql"
    store = PostgresNonceStore(engine)
    assert store.consume_nonce("") is False
    assert store.consume_nonce("x" * 129) is False
    engine.begin.assert_not_called()


def test_postgres_nonce_store_uses_atomic_conflict_safe_insert():
    engine = Mock()
    engine.dialect.name = "postgresql"
    connection = Mock()
    connection.execute.return_value.scalar_one_or_none.return_value = "one-time-nonce"
    @contextmanager
    def transaction():
        yield connection
    engine.begin.side_effect = transaction

    store = PostgresNonceStore(engine)
    assert store.consume_nonce("one-time-nonce") is True
    assert connection.execute.call_count == 2
    insert_statement = connection.execute.call_args.args[0]
    sql = str(insert_statement.compile(dialect=dialect()))
    assert "ON CONFLICT (nonce) DO NOTHING" in sql
    assert "RETURNING consumed_approval_nonces.nonce" in sql
