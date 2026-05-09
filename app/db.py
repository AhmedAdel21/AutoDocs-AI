from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings


settings = get_settings()

# pool_pre_ping: check connection is alive before using it (handles DB restarts)
# echo: log SQL in dev only — turn off in prod, it's expensive
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=settings.app_env == "development",
)

# expire_on_commit=False: don't invalidate objects after commit, we'll reload explicitly when needed
# This is the modern recommendation for async SQLAlchemy
SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base. All models inherit from this."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for DB sessions.

    Yields a session, commits on success, rolls back on exception, always closes.
    This is the canonical async-session-per-request pattern.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
