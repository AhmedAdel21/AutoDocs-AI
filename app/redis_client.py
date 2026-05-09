from redis.asyncio import Redis
from app.config import get_settings


_redis: Redis | None = None


async def get_redis() -> Redis:
    """Singleton-ish redis client. One connection pool per process."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,  # so we get str back, not bytes
            health_check_interval=30,  # auto-detects dropped connections
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
