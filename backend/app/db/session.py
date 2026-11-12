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


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings())


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
