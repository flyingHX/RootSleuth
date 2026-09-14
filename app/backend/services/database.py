import logging

from core.database import db_manager
from core.environment import get_env_bool
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def check_database_health() -> bool:
    """Check if database is healthy"""
    try:
        async with db_manager.session() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


async def initialize_database():
    """Eagerly initialize the runtime database connection when enabled."""
    # Legacy flag compatibility: only a true value skips startup; false or unset continues.
    if get_env_bool("MGX_IGNORE_INIT_DB", default=False):
        logger.info("Database startup initialization disabled")
        return
    await db_manager.ensure_connected()


async def close_database():
    """Close database connections"""
    await db_manager.close_db()
