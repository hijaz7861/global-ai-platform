"""SQLite-backed atomic nonce consumption for sandbox attestations.

Use a private persistent volume or PostgreSQL-backed implementation in production.
Do not place this database on an ephemeral filesystem or share it across hosts without
a shared transactional store. SQLite's BEGIN IMMEDIATE serializes competing writers
within one database file and prevents two workers from accepting the same nonce.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class SQLiteNonceStore:
    """Atomic single-use nonce store suitable for one host / shared SQLite file."""

    def __init__(self, database_path: str | Path, *, retention_seconds: int = 86_400) -> None:
        if retention_seconds < 930:
            raise ValueError("Nonce retention must be at least 930 seconds")
        self._path = str(database_path)
        self._retention_seconds = retention_seconds
        if self._path == ":memory:":
            raise ValueError("Nonce store requires a persistent SQLite database file")
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS consumed_attestation_nonces (
                    nonce TEXT PRIMARY KEY,
                    consumed_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_consumed_attestation_nonces_time "
                "ON consumed_attestation_nonces(consumed_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def consume_nonce(self, nonce: str) -> bool:
        """Atomically accept a nonce once. Any storage error propagates (fail closed)."""
        if not nonce or len(nonce) > 128:
            return False
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM consumed_attestation_nonces WHERE consumed_at < ?",
                    (now - self._retention_seconds,),
                )
                connection.execute(
                    "INSERT INTO consumed_attestation_nonces(nonce, consumed_at) VALUES (?, ?)",
                    (nonce, now),
                )
                connection.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                return False
            except Exception:
                connection.execute("ROLLBACK")
                raise


class PostgresNonceStore:
    """Atomic, cross-worker single-use nonce storage backed by PostgreSQL."""

    def __init__(self, db) -> None:
        from app.models import ConsumedApprovalNonce

        if db.get_bind().dialect.name != "postgresql":
            raise RuntimeError("repository approval requires PostgreSQL nonce storage")
        self._db = db
        self._model = ConsumedApprovalNonce

    def consume_nonce(self, nonce: str) -> bool:
        """Insert once using the primary-key constraint; caller owns the transaction."""
        from sqlalchemy import delete
        from sqlalchemy.dialects.postgresql import insert

        if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
            return False
        now = int(time.time())
        self._db.execute(
            delete(self._model).where(self._model.consumed_at < now - 86_400)
        )
        statement = (
            insert(self._model)
            .values(nonce=nonce, consumed_at=now)
            .on_conflict_do_nothing(index_elements=[self._model.nonce])
            .returning(self._model.nonce)
        )
        result = self._db.execute(statement)
        return result.scalar_one_or_none() is not None
