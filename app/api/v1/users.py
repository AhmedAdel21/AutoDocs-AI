import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status, Query, Request
from sqlalchemy import literal, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequestError, ConflictError, NotFoundError
from app.db import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserListResponse
from app.security import hash_password


from app.api.cursor import decode_cursor, encode_cursor
from datetime import datetime, timezone


from app.models.audit_log import AuditAction
from app.services.audit import write_audit
from app.schemas.user import UserUpdate

from app.auth.dependencies import ForbiddenError, get_current_user, require_role
from app.models.user import UserRole

from app.middleware.idempotency import idempotent_post
from app.redis_client import get_redis
from redis.asyncio import Redis
from fastapi.responses import JSONResponse

from app.models.audit_log import AuditLog
from app.schemas.user import AuditLogListResponse, AuditLogRead


router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_user(
    payload: UserCreate,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Creates a user.

    Status codes:
    - 201: created (returns user)
    - 409: email already exists
    - 422: validation error (Pydantic auto-handles)
    """

    async def _do_create():
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

        await write_audit(
            db,
            action=AuditAction.USER_CREATED,
            target_user_id=user.id,
            actor_user_id=current_user.id,
            details={"email": user.email, "role": user.role.value},
        )
        await db.refresh(user)

        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=UserRead.model_validate(user).model_dump(mode="json"),
        )

    return await idempotent_post(request, redis, _do_create)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Get a user by ID",
)
async def get_user(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Returns a single user.

    ABAC: users can read themselves; admins and leads can read anyone.

        Status codes:
        - 200: found
        - 404: not found OR soft-deleted (we don't leak which)
    """
    is_self = current_user.id == user_id
    is_privileged = current_user.role in (UserRole.ADMIN, UserRole.LEAD)
    if not (is_self or is_privileged):
        raise ForbiddenError("you can only view your own profile")

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
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> UserListResponse:
    """Lists active users, newest first.

    Cursor pagination via opaque base64-encoded (created_at, id) tuple.
    Why not offset? Offset scans-and-discards at scale; cursor uses the index.
    """
    stmt = select(User).where(User.deleted_at.is_(None))

    if cursor:
        # Any malformed input here (bad base64, bad JSON, missing keys,
        # bad datetime/UUID) all surface as ValueError/KeyError. Translate
        # to a 400 with the D010 envelope instead of leaking a 500.
        try:
            decoded = decode_cursor(cursor)
            cursor_created_at = datetime.fromisoformat(decoded["created_at"])
            cursor_id = uuid.UUID(decoded["id"])
        except (ValueError, KeyError, TypeError):
            raise BadRequestError("invalid cursor", {"field": "cursor"})
        # Row-value comparison: emits SQL `(created_at, id) < (:c, :i)`.
        # tuple_() is required on both sides — a Python tuple here would only
        # compare the first column and silently drop the id tiebreaker.
        # literal() wraps scalars as BindParameter so tuple_() accepts them.
        stmt = stmt.where(
            tuple_(User.created_at, User.id)
            < tuple_(literal(cursor_created_at), literal(cursor_id))
        )

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


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Partially update a user",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Partial update with optimistic locking.

    Status codes:
    - 200: updated
    - 404: user not found or soft-deleted
    - 409: version mismatch (concurrent update by another client)
    - 422: validation error
    """
    stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user", str(user_id))

    # Optimistic locking check
    if user.version != payload.version:
        raise ConflictError(
            "version mismatch — user was modified by another client",
            {
                "expected_version": payload.version,
                "current_version": user.version,
            },
        )

    # Capture before-state for audit
    before = {
        "full_name": user.full_name,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "is_active": user.is_active,
    }

    # Apply only the fields that were sent (partial update)
    update_data = payload.model_dump(exclude_unset=True, exclude={"version"})
    for field, value in update_data.items():
        setattr(user, field, value)

    user.version += 1  # bump for next optimistic-lock check

    after = {
        "full_name": user.full_name,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "is_active": user.is_active,
    }

    await write_audit(
        db,
        action=AuditAction.USER_UPDATED,
        target_user_id=user.id,
        # actor_user_id will be filled when auth lands (Hour 2)
        details={
            "before": before,
            "after": after,
            "fields_changed": list(update_data.keys()),
        },
    )

    await db.flush()
    await db.refresh(user)
    return user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft delete a user",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def delete_user(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Soft delete. Idempotent: returns 204 whether the user is active or already deleted.

    Returns 404 only if the user never existed.
    """
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user", str(user_id))

    if user.deleted_at is not None:
        # Already deleted — return 204, idempotent. No audit entry.
        return

    user.deleted_at = datetime.now(timezone.utc)
    user.is_active = False
    user.version += 1

    await write_audit(
        db,
        action=AuditAction.USER_DELETED,
        target_user_id=user.id,
        details={"deleted_at": user.deleted_at.isoformat()},
    )

    await db.flush()


@router.post(
    "/{user_id}:restore",
    response_model=UserRead,
    summary="Restore a soft-deleted user",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def restore_user(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Restore a soft-deleted user.

    Status codes:
    - 200: restored
    - 404: user never existed
    - 409: user is not deleted (nothing to restore)
    """
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user", str(user_id))

    if user.deleted_at is None:
        raise ConflictError(
            "user is not deleted, nothing to restore",
            {"user_id": str(user_id)},
        )

    user.deleted_at = None
    user.is_active = True
    user.version += 1

    await write_audit(
        db,
        action=AuditAction.USER_RESTORED,
        target_user_id=user.id,
    )

    await db.flush()
    await db.refresh(user)
    return user


@router.get(
    "/{user_id}/audit-log",
    response_model=AuditLogListResponse,
    summary="Audit log for a user (admin only)",
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def get_user_audit_log(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> AuditLogListResponse:
    """All audit log entries where this user was the target. Cursor paginated.

    Why audit logs are admin-only: log entries can contain sensitive context
    (e.g., email enumeration via failed logins). Restrict access.
    """
    stmt = select(AuditLog).where(AuditLog.target_user_id == user_id)

    if cursor:
        decoded = decode_cursor(cursor)
        cursor_created_at = datetime.fromisoformat(decoded["created_at"])
        cursor_id = uuid.UUID(decoded["id"])
        stmt = stmt.where(
            (AuditLog.created_at, AuditLog.id) < (cursor_created_at, cursor_id)
        )

    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
        limit + 1
    )
    rows = (await db.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id)
        if has_more and items
        else None
    )

    return AuditLogListResponse(
        items=[AuditLogRead.model_validate(a) for a in items],
        next_cursor=next_cursor,
    )
