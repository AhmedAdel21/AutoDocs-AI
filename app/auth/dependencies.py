import uuid
from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.auth.tokens import decode_token
from app.db import get_db
from app.models.user import User, UserRole
from app.redis_client import get_redis


bearer_scheme = HTTPBearer(auto_error=False)


class UnauthenticatedError(APIError):
    def __init__(self, message: str = "authentication required"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, "unauthenticated", message)


class ForbiddenError(APIError):
    def __init__(self, message: str = "insufficient privileges"):
        super().__init__(status.HTTP_403_FORBIDDEN, "forbidden", message)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> User:
    """Resolves the current user from the bearer token.

    Steps:
    1. Token present? → 401 if not.
    2. Token decodable? → 401 if not (covers expired, malformed, bad signature).
    3. Token type is access? → 401 if it's a refresh token (refresh ≠ access).
    4. JTI not in denylist? → 401 if revoked.
    5. User still exists and is active? → 401 if not.
    """
    if credentials is None:
        raise UnauthenticatedError()

    try:
        payload = decode_token(credentials.credentials)
    except JWTError as e:
        raise UnauthenticatedError(f"invalid token: {e}")

    if payload.get("type") != "access":
        raise UnauthenticatedError("not an access token")

    jti = payload.get("jti")
    if jti is None:
        raise UnauthenticatedError("token missing jti")

    # Denylist check
    if await redis.get(f"denylist:jti:{jti}"):
        raise UnauthenticatedError("token revoked")

    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise UnauthenticatedError("token missing sub")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise UnauthenticatedError("invalid sub")

    stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthenticatedError("user not found or inactive")

    return user


def require_role(*allowed_roles: UserRole):
    """Factory that returns a dependency enforcing role membership.

    Usage:
        @router.delete(..., dependencies=[Depends(require_role(UserRole.ADMIN))])
    """

    async def _dep(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role not in allowed_roles:
            raise ForbiddenError(
                f"requires one of: {', '.join(r.value for r in allowed_roles)}"
            )
        return current_user

    return _dep
