import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ConflictError, NotFoundError
from app.db import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead
from app.security import hash_password


router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
)
async def create_user(
    payload: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Creates a user.

    Status codes:
    - 201: created (returns user)
    - 409: email already exists
    - 422: validation error (Pydantic auto-handles)
    """
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    try:
        await db.flush()  # forces the INSERT, surfaces unique-violation now not on commit
    except IntegrityError as e:
        # Postgres unique violation on email
        if "users_email_key" in str(e.orig):
            raise ConflictError(
                "email already registered",
                {"field": "email", "value": payload.email},
            )
        raise

    await db.refresh(user)
    return user


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Get a user by ID",
)
async def get_user(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Returns a single user.

    Status codes:
    - 200: found
    - 404: not found OR soft-deleted (we don't leak which)
    """
    stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user", str(user_id))
    return user
