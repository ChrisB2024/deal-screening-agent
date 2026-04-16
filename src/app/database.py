"""Database session factory and dependency for FastAPI.

Provides an async session generator for use as a FastAPI dependency.
All queries go through this — ensures consistent connection pooling and
transaction management.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

import os

# Fly Postgres uses internal networking — no SSL needed
_connect_args: dict = {}
if os.environ.get("FLY_APP_NAME"):
    _connect_args["ssl"] = False

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields a database session.

    Usage: db: AsyncSession = Depends(get_db)
    The session is committed on success and rolled back on exception.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
