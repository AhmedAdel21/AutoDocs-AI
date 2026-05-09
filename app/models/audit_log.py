import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, DateTime, ForeignKey, func, Index, desc
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditAction(StrEnum):
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    USER_RESTORED = "user.restored"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_LOGIN_SUCCESS = "user.login_success"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    __table_args__ = (
        Index(
            "ix_audit_logs_target_created_id",
            "target_user_id",
            desc("created_at"),
            desc("id"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # The user this log is ABOUT (the resource being modified)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The user who PERFORMED the action (the actor)
    # Nullable because system-initiated actions exist (cleanup jobs, scheduled tasks)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action: Mapped[AuditAction] = mapped_column(String(64), nullable=False, index=True)

    # JSONB so we can query into it later (e.g., "all logs where details->>'old_role' = 'admin'")
    # JSONB > JSON in Postgres: binary storage, indexable, supports operators
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    ip_address: Mapped[str | None] = mapped_column(
        String(45), nullable=True
    )  # 45 = IPv6 max length
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,  # we paginate audit logs by time
    )
