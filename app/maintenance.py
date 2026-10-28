"""One-shot scheduled maintenance entry point for cron or a platform CronJob."""
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.db import SessionLocal
from app.services.order_expiry import expire_due_orders
from app.services.security_maintenance import cleanup_expired_security_records


def run_maintenance_once(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    order_limit: int = 100,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one bounded, transactionally committed maintenance batch."""
    if not 1 <= order_limit <= 500:
        raise ValueError("order_limit must be between 1 and 500")
    with session_factory() as db:
        with db.begin():
            expired_order_ids = expire_due_orders(db, limit=order_limit, now=now)
            cleanup_counts = cleanup_expired_security_records(db, now=now)
            record_audit_event(
                db,
                actor_user_id=None,
                organization_id=None,
                action="platform.maintenance_completed",
                entity_type="maintenance_job",
                entity_id="scheduled-maintenance",
                details={"status": "completed"},
            )
        return {
            "expired_orders": len(expired_order_ids),
            "expired_order_ids": expired_order_ids,
            "security_cleanup": cleanup_counts,
        }


def main() -> None:
    print(json.dumps(run_maintenance_once(), sort_keys=True))


if __name__ == "__main__":
    main()