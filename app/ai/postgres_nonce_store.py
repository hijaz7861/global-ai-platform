"""PostgreSQL-backed atomic nonce consumption for shared approval replay protection.

The table is created by Alembic migration 0034. Use one shared PostgreSQL
database across all API/worker replicas. Storage errors propagate (fail closed).
"""
from __future__ import annotations

import time

from sqlalchemy import BigInteger, Column, MetaData, String, Table, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


_metadata = MetaData()
_consumed_nonces = Table(
    "consumed_approval_nonces",
    _metadata,
    Column("nonce", String(128), primary_key=True),
    Column("consumed_at", BigInteger, nullable=False),
)


class PostgresNonceStore:
    """Shared, atomic single-use nonce store backed by PostgreSQL."""

    def __init__(self, engine: Engine, *, retention_seconds: int = 86_400) -> None:
        if retention_seconds < 930:
            raise ValueError("Nonce retention must be at least 930 seconds")
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgresNonceStore requires a PostgreSQL engine")
        self._engine = engine
        self._retention_seconds = retention_seconds

    def consume_nonce(self, nonce: str) -> bool:
        """Return True once; return False for duplicates/invalid values."""
        if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
            return False
        now = int(time.time())
        # A single transaction makes cleanup and insertion atomic. PostgreSQL's
        # unique primary key plus ON CONFLICT handles simultaneous workers.
        with self._engine.begin() as connection:
            connection.execute(
                delete(_consumed_nonces).where(
                    _consumed_nonces.c.consumed_at < now - self._retention_seconds
                )
            )
            statement = (
                pg_insert(_consumed_nonces)
                .values(nonce=nonce, consumed_at=now)
                .on_conflict_do_nothing(index_elements=["nonce"])
                .returning(_consumed_nonces.c.nonce)
            )
            return connection.execute(statement).scalar_one_or_none() is not None
