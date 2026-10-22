"""Small append-only audit log helper for finance/admin actions."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any


def mm_timestamp() -> str:
    tz = timezone(timedelta(hours=6, minutes=30))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


async def log_audit(
    db,
    *,
    major: str,
    actor_id: int,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    target_user_id: int | None = None,
    target_name: str | None = None,
    details: dict[str, Any] | None = None,
    semester: str | int | None = None,
) -> None:
    """Persist an append-only activity item; callers should not fail the main action if logging fails."""
    if db is None:
        return
    entry = {
        "major": str(major).lower(),
        "actor_id": int(actor_id),
        "action": str(action),
        "entity_type": str(entity_type),
        "entity_id": str(entity_id) if entity_id is not None else None,
        "target_user_id": int(target_user_id) if target_user_id is not None else None,
        "target_name": str(target_name) if target_name is not None else None,
        "details": details or {},
        "timestamp": mm_timestamp(),
        "semester": semester,
    }
    try:
        await db.audit_logs.insert_one(entry)
    except Exception:
        # Audit logging is best-effort; do not turn a successful payment into a reported failure.
        import logging
        logging.getLogger(__name__).exception("Could not write audit log entry")
