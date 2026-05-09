import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import jwt, JWTError

from app.config import get_settings


TokenType = Literal["access", "refresh"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(
    *,
    user_id: uuid.UUID,
    role: str,
    token_type: TokenType,
    jti: str | None = None,
) -> tuple[str, datetime]:
    """Returns (token, expiry_datetime)."""
    settings = get_settings()

    if token_type == "access":
        expires_delta = timedelta(minutes=settings.jwt_access_ttl_minutes)
    else:
        expires_delta = timedelta(days=settings.jwt_refresh_ttl_days)

    now = _now()
    expires_at = now + expires_delta

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti or str(uuid.uuid4()),  # unique token ID, used for revocation
    }

    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_token(token: str) -> dict[str, Any]:
    """Returns the payload. Raises JWTError on invalid signature, expired, or malformed."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
