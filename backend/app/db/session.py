"""异步 engine/session 工厂。MySQL 不可用时降级为 None，接口仍可用（不落库）。"""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Base

logger = get_logger(__name__)


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def available(self) -> bool:
        return self.session_factory is not None

    async def startup(self) -> None:
        try:
            self.engine = create_async_engine(
                self.settings.mysql_dsn, pool_size=5, pool_recycle=3600
            )
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self.session_factory = async_sessionmaker(
                self.engine, expire_on_commit=False
            )
            logger.info("mysql_connected", database=self.settings.mysql_database)
        except Exception as e:  # noqa: BLE001
            logger.warning("mysql_unavailable_degrade", error=str(e))
            self.engine = None
            self.session_factory = None

    async def shutdown(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()

    def session(self) -> AsyncSession:
        if self.session_factory is None:
            raise RuntimeError("数据库不可用")
        return self.session_factory()
