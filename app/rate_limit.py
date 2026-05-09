"""Rate limiting via slowapi + Redis.

Rate limits are per-key. Default key = client IP. We override per-endpoint to
add email-based limits to login (defends against credential stuffing across IPs).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings


def _key_func(request):
    """Default rate-limit key: client IP."""
    return get_remote_address(request)


settings = get_settings()
limiter = Limiter(
    key_func=_key_func,
    storage_uri=settings.redis_url,
    default_limits=[],  # endpoints opt in explicitly; no global default
    # headers_enabled=True makes slowapi emit Retry-After (on 429) plus
    # X-RateLimit-Limit / -Remaining / -Reset on every limited response.
    # Without this, the default _rate_limit_exceeded_handler returns a bare
    # 429 with no machine-readable cooldown signal — clients have to guess.
    headers_enabled=True,
)
