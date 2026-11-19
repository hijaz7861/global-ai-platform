"""Durable database-coordinated scheduler for bounded platform maintenance.

The last committed platform.maintenance_completed audit event is the
persistent schedule checkpoint. PostgreSQL transaction-level advisory locks
serialize replicas in the same transaction as the maintenance work.
SQLite is supported for local single-process development only.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import time
from threading import Lock
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings, validate_runtime_settings
from app.db import SessionLocal
from app.maintenance import run_maintenance_in_session
from app.models import AuditEvent

_LOCK_NAME = "hijaz-one:platform-maintenance:v1"
_LOCAL_LOCK = Lock()


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive DateTime values and aware timestamps to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@contextmanager
def _scheduler_transaction(
    db: Session,
    *,
    lock_name: str = _LOCK_NAME,
) -> Iterator[bool]:
    """Hold the scheduler lock through the same transaction as maintenance."""
    if not lock_name or len(lock_name) > 128:
        raise ValueError("Invalid scheduler lock name")

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        # The transaction-level lock is released automatically on commit,
        # rollback, or connection loss. Maintenance runs on this same session.
        with db.begin():
            acquired = bool(
                db.scalar(
                    text(
                        "SELECT pg_try_advisory_xact_lock("
                        "hashtextextended(:lock_name, 0))"
                    ),
                    {"lock_name": lock_name},
                )
            )
            yield acquired
        return

    if dialect == "sqlite":
        # SQLite has no cross-process advisory lock. This is intentionally
        # limited to one local scheduler process and held through commit.
        acquired = _LOCAL_LOCK.acquire(blocking=False)
        try:
            with db.begin():
                yield acquired
        finally:
            if acquired:
                _LOCAL_LOCK.release()
        return

    raise RuntimeError("Maintenance scheduler supports PostgreSQL or local SQLite only")


def run_scheduler_once(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    interval_seconds: int = 3600,
    order_limit: int = 100,
    now: datetime | None = None,
    lock_name: str = _LOCK_NAME,
) -> dict[str, Any]:
    """Run one due maintenance batch, or return a non-error skip status.

    A committed audit event is the durable last-success checkpoint. Missed
    intervals coalesce into one bounded run rather than a catch-up burst.
    """
    if not 60 <= interval_seconds <= 86_400:
        raise ValueError("interval_seconds must be between 60 and 86400")
    if not 1 <= order_limit <= 500:
        raise ValueError("order_limit must be between 1 and 500")

    current = _as_utc(now or datetime.now(timezone.utc))
    with session_factory() as db:
        with _scheduler_transaction(db, lock_name=lock_name) as acquired:
            if not acquired:
                return {"status": "locked"}

            last_completed_at = db.scalar(
                select(AuditEvent.created_at)
                .where(AuditEvent.action == "platform.maintenance_completed")
                .order_by(AuditEvent.created_at.desc())
                .limit(1)
            )
            if last_completed_at is not None:
                next_run_at = _as_utc(last_completed_at) + timedelta(
                    seconds=interval_seconds
                )
                if current < next_run_at:
                    return {
                        "status": "not_due",
                        "next_run_at": next_run_at.isoformat(),
                    }

            result = run_maintenance_in_session(
                db,
                order_limit=order_limit,
                now=current,
            )
            # Do not emit order IDs or memory content to scheduler logs.
            return {
                "status": "completed",
                "expired_orders": int(result["expired_orders"]),
                "security_cleanup": {
                    key: int(value)
                    for key, value in result["security_cleanup"].items()
                    if isinstance(value, int)
                },
                "expired_memory_records_deleted": int(
                    result["expired_memory_records_deleted"]
                ),
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HIJAZ ONE durable maintenance scheduler"
    )
    parser.add_argument("--once", action="store_true", help="Check and run at most once")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=settings.maintenance_poll_seconds,
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=settings.maintenance_interval_seconds,
    )
    parser.add_argument(
        "--order-limit",
        type=int,
        default=settings.maintenance_order_limit,
    )
    args = parser.parse_args(argv)
    if not 1 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds must be between 1 and 300")
    if not 60 <= args.interval_seconds <= 86_400:
        parser.error("--interval-seconds must be between 60 and 86400")
    if not 1 <= args.order_limit <= 500:
        parser.error("--order-limit must be between 1 and 500")

    validate_runtime_settings(settings)
    while True:
        try:
            result = run_scheduler_once(
                interval_seconds=args.interval_seconds,
                order_limit=args.order_limit,
            )
            if args.once or result["status"] == "completed":
                print(json.dumps(result, sort_keys=True), flush=True)
        except Exception:
            # Database/provider exception text can contain connection details.
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error_code": "maintenance_scheduler_iteration_failed",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
