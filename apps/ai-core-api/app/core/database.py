from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings

settings = get_settings()

engine_options = {
    "echo": settings.debug,
    "future": True,
    "pool_pre_ping": True,
}

if settings.database_url.startswith("postgresql"):
    engine_options.update(
        {
            "pool_recycle": 300,
            "pool_timeout": 10,
            "connect_args": {
                "timeout": 10,
                "command_timeout": 30,
            },
        }
    )

engine = create_async_engine(settings.database_url, **engine_options)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
