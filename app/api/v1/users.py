import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ConflictError, NotFoundError
from app.db import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserListResponse
from app.security import hash_password


from app.api.cursor import decode_cursor, encode_cursor
from datetime import datetime


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
        # Postgres unique violation on email.
        # Constraint name is "ix_users_email" because the column has both
        # unique=True and index=True — SQLAlchemy emits a UNIQUE INDEX (ix_*),
        # not a UNIQUE CONSTRAINT (which PG would name *_key).
        if "ix_users_email" in str(e.orig):
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


@router.get(
    "",
    response_model=UserListResponse,
    summary="List users (cursor pagination)",
)
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> UserListResponse:
    """Lists active users, newest first.

    Cursor pagination via opaque base64-encoded (created_at, id) tuple.
    Why not offset? Offset scans-and-discards at scale; cursor uses the index.
    """
    stmt = select(User).where(User.deleted_at.is_(None))

    if cursor:
        decoded = decode_cursor(cursor)
        cursor_created_at = datetime.fromisoformat(decoded["created_at"])
        cursor_id = uuid.UUID(decoded["id"])
        # Tuple comparison: rows strictly after the cursor
        stmt = stmt.where((User.created_at, User.id) < (cursor_created_at, cursor_id))

    stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)

    rows = (await db.execute(stmt)).scalars().all()

    # Fetch one extra to know if there's a next page
    has_more = len(rows) > limit
    items = list(rows[:limit])

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

    return UserListResponse(
        items=[UserRead.model_validate(u) for u in items],
        next_cursor=next_cursor,
    )
