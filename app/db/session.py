import os

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

_raw = os.getenv("DATABASE_URL", "")

if "${" in _raw or not _raw:
    _user = os.getenv("POSTGRES_USER", "appuser")
    _pass = os.getenv("POSTGRES_PASSWORD", "changeme_strong_password")
    _host = os.getenv("POSTGRES_HOST", "localhost")
    _port = os.getenv("POSTGRES_PORT", "5432")
    _db = os.getenv("POSTGRES_DB", "appdb")
    DATABASE_URL = f"postgresql+asyncpg://{_user}:{_pass}@{_host}:{_port}/{_db}"
else:
    DATABASE_URL = _raw
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)


async def create_db_and_tables():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncSession:
    async with AsyncSession(engine) as session:
        yield session
