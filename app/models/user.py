import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, DateTime, Integer, Boolean, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(StrEnum):
    """User roles. StrEnum so the value IS the string ('engineer' not UserRole.ENGINEER)."""

    ENGINEER = "engineer"
    LEAD = "lead"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    # UUID v4 as primary key.
    # Why not autoincrement int? Distributed-system friendly, no leak of "how many users
    # have we created", harder to enumerate in a brute-force attack.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Hashed password. Never store plaintext. None = OAuth-only user (Day 2).
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        String(32),
        nullable=False,
        default=UserRole.ENGINEER,
    )

    # Soft delete. We never hard-delete users in regulated/audit contexts.
    # NULL = active. Non-NULL = deleted at this timestamp.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,  # indexed because every list query filters WHERE deleted_at IS NULL
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optimistic locking. Bumped on every UPDATE.
    # Prevents lost updates when two clients edit the same row concurrently.
    # Client sends the version it read; if it doesn't match, we 409 Conflict.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role}>"
