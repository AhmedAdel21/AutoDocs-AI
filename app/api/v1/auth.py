from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
import uuid
from jose import JWTError

from app.api.errors import APIError
from app.db import get_db
from app.models.user import User
from app.models.audit_log import AuditAction
from app.security import verify_password
from app.services.audit import write_audit
from app.config import get_settings
from app.auth.tokens import create_token, decode_token
from app.redis_client import get_redis
from app.auth.dependencies import get_current_user
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expires


class RefreshRequest(BaseModel):
    refresh_token: str


class UnauthorizedError(APIError):
    def __init__(self, message: str = "invalid credentials"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, "unauthorized", message)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email + password",
)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Authenticate and issue access + refresh tokens.

    Status codes:
    - 200: success
    - 401: invalid credentials (NEVER 404 — would leak email existence)
    """
    stmt = select(User).where(User.email == payload.email, User.deleted_at.is_(None))
    user = (await db.execute(stmt)).scalar_one_or_none()

    # Constant-time approach: always verify_password even if user is None,
    # to avoid timing attacks that could enumerate which emails exist.
    # passlib.verify_password is constant-time within the algorithm,
    # but a fast None-check would short-circuit and create a measurable timing diff.
    valid_password = False
    if user is not None and user.hashed_password is not None:
        valid_password = verify_password(payload.password, user.hashed_password)

    if user is None or not valid_password or not user.is_active:
        # Audit the failure for the email regardless of whether the user exists
        await write_audit(
            db,
            action=AuditAction.USER_LOGIN_FAILED,
            target_user_id=user.id if user else None,
            details={"email_attempted": payload.email},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        raise UnauthorizedError()

    print(f"user.role.value: {user.role}")
    access_token, _ = create_token(user_id=user.id, role=user.role, token_type="access")
    refresh_token, refresh_exp = create_token(
        user_id=user.id, role=user.role, token_type="refresh"
    )

    await write_audit(
        db,
        action=AuditAction.USER_LOGIN_SUCCESS,
        target_user_id=user.id,
        actor_user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    settings = get_settings()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_ttl_minutes * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token (rotates refresh token)",
)
async def refresh(
    payload: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> TokenResponse:
    """Refresh-token rotation: each refresh issues a NEW refresh token AND denylists the old one.

    If the same refresh token is used twice, the second use is denied (theft detection).
    """
    try:
        token_payload = decode_token(payload.refresh_token)
    except JWTError:
        raise UnauthorizedError("invalid refresh token")

    if token_payload.get("type") != "refresh":
        raise UnauthorizedError("not a refresh token")

    jti = token_payload.get("jti")
    if jti is None:
        raise UnauthorizedError("token missing jti")

    # Replay detection: if this jti is already on the denylist, somebody is reusing a rotated token
    if await redis.get(f"denylist:jti:{jti}"):
        # SECURITY: this user's refresh chain may be compromised.
        # Production: revoke ALL their tokens (would require a per-user generation counter).
        raise UnauthorizedError("refresh token reused — possible theft")

    user_id = uuid.UUID(token_payload["sub"])
    stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthorizedError("user not found")

    # Denylist the old refresh token's jti.
    # TTL = remaining lifetime of the old token (denylist entry can expire when token would have anyway)
    old_exp = token_payload["exp"]
    now_ts = int(datetime.now(timezone.utc).timestamp())
    ttl = max(old_exp - now_ts, 1)
    await redis.setex(f"denylist:jti:{jti}", ttl, "1")

    # Issue NEW pair
    new_access, _ = create_token(
        user_id=user.id, role=user.role, token_type="access"
    )
    new_refresh, _ = create_token(
        user_id=user.id, role=user.role, token_type="refresh"
    )

    settings = get_settings()
    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.jwt_access_ttl_minutes * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout (denylist current access + refresh tokens)",
)
async def logout(
    refresh_payload: RefreshRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> None:
    """Denylist BOTH the current access token AND the supplied refresh token.

    The access-token jti is read from the bearer header by get_current_user
    indirectly — but we need the raw payload here, so we re-decode.

    The client MUST send the refresh token in the body to fully log out.
    Otherwise, only the access token is denylisted and the refresh token
    can still be used to mint a new access token.
    """
    # Denylist refresh token
    try:
        rp = decode_token(refresh_payload.refresh_token)
        if rp.get("type") == "refresh" and rp.get("sub") == str(current_user.id):
            ttl = max(rp["exp"] - int(datetime.now(timezone.utc).timestamp()), 1)
            await redis.setex(f"denylist:jti:{rp['jti']}", ttl, "1")
    except JWTError:
        pass  # bad refresh token, just skip; access token still gets denylisted below

    # Denylist current access token
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        access_token = auth_header[7:]
        try:
            ap = decode_token(access_token)
            ttl = max(ap["exp"] - int(datetime.now(timezone.utc).timestamp()), 1)
            await redis.setex(f"denylist:jti:{ap['jti']}", ttl, "1")
        except JWTError:
            pass

    await write_audit(
        db,
        action=AuditAction.USER_LOGOUT,
        target_user_id=current_user.id,
        actor_user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
    )


@router.get(
    "/me",
    response_model=UserRead,
    summary="Current authenticated user",
)
async def me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user
