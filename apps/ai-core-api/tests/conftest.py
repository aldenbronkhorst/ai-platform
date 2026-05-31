import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

# Register UUID type support for SQLite DDL compiler (models use PostgreSQL UUID)
def visit_uuid(self, type_, **kw):
    return "CHAR(36)"

SQLiteTypeCompiler.visit_UUID = visit_uuid

from app.main import app
from app.core.config import get_settings
from app.core.database import Base, get_db

# Force debug/test override at test runtime to allow anonymous localhost/test bypass
os.environ["DEBUG"] = "true"
get_settings.cache_clear()

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)
TestingSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def override_get_db():
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    import asyncio
    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
    async def _drop_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    asyncio.run(_create_tables())
    yield
    asyncio.run(_drop_tables())


@pytest.fixture(autouse=True)
def apply_db_override():
    """Applies the SQLite in-memory DB override before every test and clears overrides after.

    This ensures that:
    1. Every test runs against a clean SQLite in-memory database to avoid needing a live PostgreSQL.
    2. Overrides do not leak across test modules.
    """
    app.dependency_overrides[get_db] = override_get_db
    get_settings.cache_clear()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()
