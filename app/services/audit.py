import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog, AuditAction


async def write_audit(
    db: AsyncSession,
    *,
    action: AuditAction,
    target_user_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Add an audit log entry to the session.

    Important: does NOT commit. The calling endpoint owns the transaction —
    if the endpoint fails, the audit log rolls back with it. This is intentional:
    we never want an audit entry for a state change that didn't happen.
    """
    entry = AuditLog(
        action=action,
        target_user_id=target_user_id,
        actor_user_id=actor_user_id,
        details=details or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
