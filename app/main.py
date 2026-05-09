from fastapi import FastAPI, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager


from app.config import get_settings
from app.db import get_db
from app.redis_client import close_redis

from app.observability import setup_tracing

from app.api.v1 import api_router
from app.logging_config import setup_logging
from app.middleware.request_id import RequestIdMiddleware
from app.rate_limit import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from redis.asyncio import Redis
from app.redis_client import get_redis
from app.api.errors import APIError
from app.logging_config import log
from app.db import engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: nothing yet (redis is lazy-connected)
    log.info("app.startup", env=get_settings().app_env)
    yield
    # Graceful shutdown — runs when SIGTERM received
    log.info("app.shutdown.started")
    await engine.dispose()  # close Postgres pool, drain in-flight queries
    await close_redis()
    log.info("app.shutdown.complete")


def create_app() -> FastAPI:
    """App factory pattern.

    Why a factory and not a module-level `app = FastAPI()`?
    - Tests can build their own app with overridden settings.
    - Multi-process workers (uvicorn --workers) get fresh state per process.
    - It mirrors the pattern Flask popularized; FastAPI inherits it.
    """

    setup_logging()  # MUST be before any logging happens
    settings = get_settings()

    app = FastAPI(
        title="AutoDocs AI",
        version="0.1.0",
        description="Internal Q&A platform for automotive engineers",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Order matters: request_id binds context BEFORE tracing
    app.add_middleware(RequestIdMiddleware)

    # Tracing — must be after FastAPI() construction, before routes
    setup_tracing(app)

    app.include_router(api_router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe. Does NOT check DB — that's /health/ready."""
        return {"status": "ok", "env": settings.app_env}

    @app.get("/health/ready", tags=["meta"])
    async def ready(
        db: AsyncSession = Depends(get_db),
    ) -> dict[str, str | dict[str, str]]:
        """Readiness probe — checks all critical dependencies.

        Returns:
        - 200 with {"status": "ready", "checks": {...}} if all dependencies are reachable
        - 503 with detail if any dependency is down

        K8s/Cloud Run uses this to decide if the instance gets traffic. Fail-fast on
        a dependency outage — don't accept traffic we can't serve.
        """
        checks: dict[str, str] = {}
        all_ok = True

        # Postgres
        try:
            await db.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as e:
            checks["postgres"] = f"error: {e}"
            all_ok = False

        # Redis
        try:
            redis = await get_redis()
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e}"
            all_ok = False

        if not all_ok:
            raise APIError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "not_ready",
                "one or more dependencies are not ready",
                {"checks": checks},
            )

        return {"status": "ready", "checks": checks}

    return app


app = create_app()
