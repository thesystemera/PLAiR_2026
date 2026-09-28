from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from database.models import Base
from config import settings

ASYNC_POOL_SIZE = 20
ASYNC_MAX_OVERFLOW = 10
ASYNC_POOL_TIMEOUT_S = 30
IDLE_IN_TRANSACTION_TIMEOUT_MS = 120000

_sync_engine = create_engine(
    settings.DATABASE_URL,
    poolclass=NullPool
)

engine = create_async_engine(
    settings.DATABASE_URL.replace('postgresql://', 'postgresql+asyncpg://'),
    pool_size=ASYNC_POOL_SIZE,
    max_overflow=ASYNC_MAX_OVERFLOW,
    pool_timeout=ASYNC_POOL_TIMEOUT_S,
    pool_pre_ping=True,
    echo=False,
    connect_args={"server_settings": {"idle_in_transaction_session_timeout": str(IDLE_IN_TRANSACTION_TIMEOUT_MS)}}
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

def init_db():
    Base.metadata.create_all(bind=_sync_engine)
