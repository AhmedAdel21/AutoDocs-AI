import json
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.redis_client import get_redis


IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60  # 24 hours
IN_PROGRESS_SENTINEL = "__IN_PROGRESS__"


async def idempotent_post(
    request: Request,
    redis: Redis,
    handler: Any,  # the actual endpoint coroutine
) -> Response:
    """Wraps a POST handler with idempotency-key support.

    Behavior:
    - No Idempotency-Key header → run handler normally.
    - Key never seen → mark in-progress, run handler, store response, return.
    - Key seen with stored response → return cached response.
    - Key seen as in-progress → 409 with code 'request_in_progress'.
    """
    key = request.headers.get("idempotency-key")
    if not key:
        # No key → treat as a normal POST (no idempotency guarantees)
        return await handler()

    # Validate key shape — UUID-ish, max 128 chars
    if len(key) > 128 or not key.replace("-", "").replace("_", "").isalnum():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": {
                    "code": "invalid_idempotency_key",
                    "message": "Idempotency-Key must be alphanumeric, max 128 chars",
                    "details": None,
                }
            },
        )

    redis_key = f"idem:{request.url.path}:{key}"

    # SET with NX (set if not exists) + EX (TTL) — atomic check-and-set
    acquired = await redis.set(
        redis_key,
        IN_PROGRESS_SENTINEL,
        nx=True,
        ex=IDEMPOTENCY_TTL_SECONDS,
    )

    if not acquired:
        existing = await redis.get(redis_key)
        if existing == IN_PROGRESS_SENTINEL:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "error": {
                        "code": "request_in_progress",
                        "message": "A request with this Idempotency-Key is currently being processed",
                        "details": None,
                    }
                },
            )
        # Cached final response
        cached = json.loads(existing)
        return JSONResponse(status_code=cached["status"], content=cached["body"])

    # We won the lock. Run the handler.
    try:
        response = await handler()
        # Read response body to cache it.
        # FastAPI returns a Response object; we serialize what's needed.
        body = json.loads(response.body) if hasattr(response, "body") else None
        cached_payload = {"status": response.status_code, "body": body}
        await redis.setex(
            redis_key,
            IDEMPOTENCY_TTL_SECONDS,
            json.dumps(cached_payload),
        )
        return response
    except Exception:
        # On error, clear the sentinel so retry can proceed
        await redis.delete(redis_key)
        raise
