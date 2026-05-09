import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class UserBase(BaseModel):
    """Shared fields. Never instantiated directly."""

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: UserRole = UserRole.ENGINEER


class UserCreate(UserBase):
    """Request body for POST /users. Includes password (write-only)."""

    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """Request body for PATCH /users/{id}. All fields optional."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None
    # Required for optimistic locking — client sends the version they read
    version: int = Field(...)


class UserRead(UserBase):
    """Response model. Excludes hashed_password."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

class UserListResponse(BaseModel):
    items: list[UserRead]
    next_cursor: str | None = None