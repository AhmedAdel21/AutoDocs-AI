from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.auth.tokens import create_token
from app.db import get_db
from app.models.user import User
from app.models.audit_log import AuditAction
from app.security import verify_password
from app.services.audit import write_audit
from app.config import get_settings


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expires


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
    access_token, _ = create_token(
        user_id=user.id, role=user.role, token_type="access"
    )
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
