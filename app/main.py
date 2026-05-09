from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager


from app.config import get_settings
from app.db import get_db
from app.redis_client import close_redis

from app.api.v1 import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: nothing yet (redis is lazy-connected)
    yield
    # shutdown
    await close_redis()


def create_app() -> FastAPI:
    """App factory pattern.

    Why a factory and not a module-level `app = FastAPI()`?
    - Tests can build their own app with overridden settings.
    - Multi-process workers (uvicorn --workers) get fresh state per process.
    - It mirrors the pattern Flask popularized; FastAPI inherits it.
    """
    settings = get_settings()

    app = FastAPI(
        title="AutoDocs AI",
        version="0.1.0",
        description="Internal Q&A platform for automotive engineers",
        lifespan=lifespan,
    )

    app.include_router(api_router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe. Does NOT check DB — that's /health/ready."""
        return {"status": "ok", "env": settings.app_env}

    @app.get("/health/ready", tags=["meta"])
    async def ready(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
        """Readiness probe. Hits the DB. K8s uses this to decide if we get traffic."""
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
