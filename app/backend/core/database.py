import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from core.config import settings
from core.environment import get_env_bool, get_env_int
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Async driver to substitute per backend when DATABASE_URL names a sync driver
# (or no driver at all), e.g. postgresql:// or postgresql+psycopg2:// -> asyncpg.
_ASYNC_DRIVER_BY_BACKEND = {
    "sqlite": "aiosqlite",
    "postgresql": "asyncpg",
    "mysql": "aiomysql",
    "mariadb": "aiomysql",
}

# Drivers that already work with SQLAlchemy's asyncio extension; URLs naming
# these are passed through untouched ("psycopg" is the async-capable psycopg 3).
_ASYNC_CAPABLE_DRIVERS = {"aiosqlite", "asyncpg", "aiomysql", "asyncmy", "psycopg", "aioodbc"}


class Base(DeclarativeBase):
    pass


class DatabaseManager:
    def __init__(self):
        self.engine: Optional[AsyncEngine] = None
        self.async_session_maker: Optional[async_sessionmaker[AsyncSession]] = None
        self._init_lock = asyncio.Lock()  # Serializes init_db/close_db

    @staticmethod
    def _sanitize_query_params(url: URL) -> URL:
        """Remove query parameters that are incompatible with asyncpg.

        Some providers (e.g. Neon) may inject parameters like ``channel_binding``
        that are not supported by asyncpg and cause connection failures.
        """
        unsupported_params = {"channel_binding"}
        found = unsupported_params & set(url.query)
        if found:
            logger.warning(f"Removed unsupported database URL query params: {sorted(found)}")
            return url.set(query={k: v for k, v in url.query.items() if k not in unsupported_params})
        return url

    @staticmethod
    def _is_transaction_pooler_url(database_url: str) -> bool:
        """Heuristically detect a transaction-mode connection pooler endpoint.

        Managed Postgres providers expose pooler hosts (often containing
        ``pooler`` / ``pgbouncer``) or accept a ``pgbouncer=true`` flag. Under
        transaction pooling, server-side prepared statements cannot be relied on,
        so callers disable asyncpg's statement cache for such URLs.
        """
        lowered = database_url.lower()
        return "pooler" in lowered or "pgbouncer" in lowered

    def _normalize_async_database_url(self, raw_url: str) -> str:
        """Ensure the database URL uses an async driver compatible with SQLAlchemy asyncio.

        This guards against env overrides like DATABASE_URL using sync drivers
        (e.g., sqlite:///, postgresql:// or postgresql+psycopg2://), which would
        otherwise load a sync DBAPI and break async engine initialization.
        """
        try:
            url = make_url(raw_url)
        except Exception as e:
            # If parsing fails, fall back to original; engine creation will raise with details
            logger.error(f"Failed to parse database URL: {e}")
            return raw_url

        backend, _, driver = (url.drivername or "").partition("+")
        if backend == "postgres":  # legacy alias some providers still emit; not a valid SQLAlchemy dialect
            backend = "postgresql"

        if driver in _ASYNC_CAPABLE_DRIVERS:
            pass  # Keep the explicit async driver
        elif backend in _ASYNC_DRIVER_BY_BACKEND:
            driver = _ASYNC_DRIVER_BY_BACKEND[backend]
        else:
            # Leave unknown schemes as-is
            logger.warning(f"Unknown database driver: {url.drivername}")
            return raw_url

        url = url.set(drivername=f"{backend}+{driver}")

        # asyncpg rejects some libpq query params that providers inject (psycopg
        # honors them, so only strip for asyncpg)
        if driver == "asyncpg":
            url = self._sanitize_query_params(url)

        if backend == "sqlite":
            self._warn_if_sqlite_file_missing(url)

        normalized = url.render_as_string(hide_password=False)
        if normalized != raw_url:
            logger.warning("Adjusted database URL driver for async compatibility")
        return normalized

    @staticmethod
    def _warn_if_sqlite_file_missing(url: URL) -> None:
        """Surface misconfigured SQLite paths early.

        SQLite silently creates a missing file on first connect, which turns a
        wrong path into an empty database instead of an error - hence the log.
        In-memory databases have no file to check.
        """
        database = url.database
        if not database or database == ":memory:":
            return
        path = Path(database)
        if path.exists():
            logger.debug(f"Database exists: {database}")
        elif not path.parent.exists():
            logger.warning(f"SQLite database directory does not exist (connection will fail): {path.parent}")
        else:
            logger.warning(f"SQLite database file not found (will be created on connect): {database}")

    def _build_engine_kwargs(self, database_url: str) -> dict:
        """Build create_async_engine() kwargs appropriate for the dialect and runtime."""
        engine_kwargs = {"echo": settings.debug}

        try:
            backend = make_url(database_url).get_backend_name()
        except Exception:
            # Unparseable URL: return bare kwargs so create_async_engine raises with details
            return engine_kwargs

        if backend == "sqlite":
            # aiosqlite picks its own pool implementation (StaticPool for :memory:),
            # which rejects QueuePool sizing kwargs, and pre-ping/recycle are
            # pointless for a local file - so no pool tuning here.
            return engine_kwargs

        # settings.is_lambda parses the template's IS_LAMBDA env flag (pydantic bool).
        is_lambda = bool(getattr(settings, "is_lambda", False))

        if is_lambda:
            if get_env_bool("DB_DISABLE_POOL", default=False):
                # Escape hatch: revert to a fresh connection per request. Use this if
                # asyncpg raises "cannot switch to state" / event-loop binding errors
                # under pooling; no code change or redeploy is needed, only the env var.
                engine_kwargs["poolclass"] = NullPool
                logger.info("Using NullPool for Lambda environment (DB_DISABLE_POOL set)")
            else:
                # A warm Lambda container serves one request at a time and is reused
                # across invocations. Keeping a tiny pool lets connections survive
                # between invocations, removing the per-request TCP+TLS+auth handshake
                # to a remote Postgres - the dominant fixed cost on every API call.
                # pool_pre_ping transparently discards stale or cross-event-loop
                # connections (issuing a fresh one) instead of failing the request,
                # and pool_recycle drops connections idle past the recycle window
                # (kept < typical serverless Postgres idle timeout).
                engine_kwargs["pool_pre_ping"] = get_env_bool("DB_POOL_PRE_PING", default=True)
                engine_kwargs["pool_size"] = get_env_int("DB_POOL_SIZE", default=1)
                engine_kwargs["max_overflow"] = get_env_int("DB_MAX_OVERFLOW", default=0)
                engine_kwargs["pool_recycle"] = get_env_int("DB_POOL_RECYCLE", default=280)
                engine_kwargs["pool_timeout"] = get_env_int("DB_POOL_TIMEOUT", default=5)
                engine_kwargs["pool_use_lifo"] = get_env_bool("DB_POOL_USE_LIFO", default=True)
                logger.info("Using pooled connections for Lambda environment (pool_pre_ping=%s, pool_size=%d, "
                            "max_overflow=%d, pool_recycle=%ds, pool_timeout=%ds, pool_use_lifo=%s)",
                            engine_kwargs["pool_pre_ping"],
                            engine_kwargs["pool_size"],
                            engine_kwargs["max_overflow"],
                            engine_kwargs["pool_recycle"],
                            engine_kwargs["pool_timeout"],
                            engine_kwargs["pool_use_lifo"]
                            )
        else:
            # Non-Lambda: Use QueuePool with connection pooling
            engine_kwargs["pool_pre_ping"] = get_env_bool("DB_POOL_PRE_PING", default=True)  # Verify connections before using them
            engine_kwargs["pool_size"] = get_env_int("DB_POOL_SIZE", default=5)  # Connection pool size
            engine_kwargs["max_overflow"] = get_env_int("DB_MAX_OVERFLOW", default=5)  # Maximum overflow connections
            engine_kwargs["pool_recycle"] = get_env_int("DB_POOL_RECYCLE", default=280)  # Connection recycle time (1 hour)
            engine_kwargs["pool_timeout"] = get_env_int("DB_POOL_TIMEOUT", default=30)  # Connection acquisition timeout (30 seconds)
            engine_kwargs["pool_use_lifo"] = get_env_bool("DB_POOL_USE_LIFO", default=True)
            logger.info("Using QueuePool for non-Lambda environment (pool_pre_ping=%s, pool_size=%d, "
                        "max_overflow=%d, pool_recycle=%ds, pool_timeout=%ds, pool_use_lifo=%s)",
                        engine_kwargs["pool_pre_ping"],
                        engine_kwargs["pool_size"],
                        engine_kwargs["max_overflow"],
                        engine_kwargs["pool_recycle"],
                        engine_kwargs["pool_timeout"],
                        engine_kwargs["pool_use_lifo"]
                        )

        # Transaction-mode poolers (e.g. PgBouncer, Neon/Supabase pooler endpoints)
        # multiplex many clients over few server connections and cannot keep
        # per-session prepared statements. asyncpg caches prepared statements by
        # default, which breaks under such poolers, so disable that cache when the
        # URL points at one. Harmless for direct connections (cache simply off).
        if "asyncpg" in database_url and self._is_transaction_pooler_url(database_url):
            engine_kwargs.setdefault("connect_args", {})["statement_cache_size"] = 0
            logger.info("Detected transaction pooler URL; disabling asyncpg statement cache")

        return engine_kwargs

    async def ensure_connected(self):
        """Ensure the engine and session factory exist.

        The initialized fast path avoids locking on every request. Initialization
        itself holds the lock and checks state again, so concurrent cold-start
        callers never build a second engine.
        """
        if self.async_session_maker is not None:
            return

        async with self._init_lock:
            if self.async_session_maker is not None:
                return

            if not settings.database_url:
                logger.error("No database URL provided. DATABASE_URL environment variable must be set.")
                raise ValueError("DATABASE_URL environment variable is required")

            try:
                database_url = self._normalize_async_database_url(settings.database_url)
                engine_kwargs = self._build_engine_kwargs(database_url)

                self.engine = create_async_engine(database_url, **engine_kwargs)
                try:
                    from core.telemetry import instrument_sqlalchemy

                    instrument_sqlalchemy(self.engine)
                except Exception:  # noqa: BLE001,S110 - diagnostics are fail-open
                    pass
                self.async_session_maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
                logger.info("Database connection initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize database: {e}", exc_info=True)
                raise

    async def get_engine(self) -> AsyncEngine:
        """Ensure the connection exists and return its engine."""
        await self.ensure_connected()
        engine = self.engine
        if engine is None:
            raise RuntimeError("Database engine not initialized")
        return engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a request-scoped session after ensuring the connection exists."""
        await self.ensure_connected()
        session_maker = self.async_session_maker
        if session_maker is None:
            raise RuntimeError("Database session factory not initialized")
        async with session_maker() as session:
            yield session

    async def close_db(self):
        """Close database connection and dispose engine.

        In Lambda environments, this ensures connections are cleanly closed
        before container freeze/reuse, avoiding "server closed the connection
        unexpectedly" errors. The initialization lock prevents shutdown from
        disposing an engine while lazy initialization is still in progress.
        """
        async with self._init_lock:
            if not self.engine:
                return  # Already closed

            try:
                await self.engine.dispose()
                logger.info("Database connection closed and engine disposed")
            except Exception as e:
                logger.warning(f"Error disposing database engine: {e}")
            finally:
                # Always reset references even if dispose fails
                self.engine = None
                self.async_session_maker = None


db_manager = DatabaseManager()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped AsyncSession.

    Transaction ownership stays with the caller (services call commit()).
    On exit - normal or exceptional - the context manager closes the session,
    which releases the connection and implicitly rolls back any uncommitted
    transaction. Don't add a manual rollback here: a second rollback on an
    already-failed asyncpg connection raises "cannot switch to state" errors.
    """
    async with db_manager.session() as session:
        yield session
