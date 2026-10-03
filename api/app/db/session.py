"""异步 SQLAlchemy 会话与建表。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models.entities import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(s.database_url, echo=False, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    """M1 用 create_all 起步；v1.0 引入 Alembic 迁移。

    注：SQLite FK 级联靠 ORM cascade（all, delete-orphan）保证；
    aiosqlite 上以同步事件监听器设置 PRAGMA foreign_keys 会产生未 await 的游标，故不启用。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _lightweight_migrations(conn)


async def _lightweight_migrations(conn) -> None:
    """M1 期间的小迁移（v1.0 引入 Alembic 后移除）：为已存在的旧库补列。"""
    from sqlalchemy import text

    res = await conn.execute(text("PRAGMA table_info(llm_channel)"))
    cols = {row[1] for row in res.fetchall()}
    if cols and "extra_headers" not in cols:
        await conn.execute(text("ALTER TABLE llm_channel ADD COLUMN extra_headers JSON NULL"))


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db() -> AsyncSession:  # FastAPI 依赖
    factory = get_session_factory()
    async with factory() as session:
        yield session
