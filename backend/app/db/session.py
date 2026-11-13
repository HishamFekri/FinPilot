"""Engine and session lifecycle. Sync SQLAlchemy with psycopg 3 (ARCHITECTURE.md §3.5)."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def build_engine(settings: Settings) -> Engine:
    """Create an engine. SQLAlchemy connects lazily, so this doesn't touch the database."""
    return create_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        # Database errors would otherwise repeat bound values, such as hashes and emails.
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": (
                f"-c statement_timeout={settings.db_statement_timeout_ms} "
                "-c idle_in_transaction_session_timeout=150000"
            ),
        },
    )


# The settings the running app was created with (create_app calls use_settings). Without this,
# the engine read a second copy from the environment: an app built with explicit settings failed
# every request that took a database session when the environment had no DATABASE_URL (CI).
_app_settings: dict[str, Settings] = {}


def use_settings(settings: Settings) -> None:
    """Points the database layer at the app's own settings, and drops an engine built for others."""
    if _app_settings.get("current") is settings:
        return
    _app_settings["current"] = settings
    get_engine.cache_clear()
    clear = getattr(_session_factory, "cache_clear", None)  # tests may replace the factory
    if clear is not None:
        clear()


@lru_cache
def get_engine() -> Engine:
    return build_engine(_app_settings.get("current") or get_settings())


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session]:
    """One transaction per request: commit on success, roll back on any exception."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
