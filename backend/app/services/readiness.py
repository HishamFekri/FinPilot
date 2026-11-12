"""Readiness: is the database reachable, and does it match this build's migrations?"""

import logging

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import BACKEND_DIR
from app.db.hardening import is_hardened

logger = logging.getLogger(__name__)


def expected_migration_heads() -> set[str]:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return set(ScriptDirectory.from_config(config).get_heads())


def is_ready(engine: Engine, *, require_hardened: bool = False) -> bool:
    """Reasons are logged and never returned, so the public endpoint reveals nothing.

    `require_hardened` is for production: a privileged connection, or missing row-level security,
    means the deployment isn't ready, however well everything else works.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = set(MigrationContext.configure(connection).get_current_heads())
            hardened = not require_hardened or is_hardened(connection)
    except SQLAlchemyError as exc:
        logger.warning("Database not reachable", extra={"error_type": type(exc).__name__})
        return False

    if current != expected_migration_heads():
        logger.warning("Database migrations don't match this build")
        return False
    if not hardened:
        logger.warning("Database role or row-level security isn't production-ready")
        return False
    return True
