"""Migration tooling checks that need no database server."""

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import BACKEND_DIR
from app.db import models as _models  # noqa: F401  # Registers the tables on Base.metadata.
from app.db.base import Base
from app.services.readiness import expected_migration_heads


def test_alembic_configuration_loads() -> None:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.file_template.startswith("%(year)d")


def test_migration_history_has_one_head() -> None:
    assert expected_migration_heads() == {"0017"}


def test_no_foreign_key_cascades_or_nulls_on_delete() -> None:
    """Cascades could later destroy audit or financial records. Erasure has its own design."""
    actions = {
        f"{table.name}.{foreign_key.parent.name}": foreign_key.ondelete
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
    }
    assert actions
    assert set(actions.values()) == {"RESTRICT"}, actions


def test_no_identity_table_has_a_column_for_a_raw_secret() -> None:
    columns = {column.name for table in Base.metadata.tables.values() for column in table.columns}
    assert not columns & {"password", "token", "session_token", "access_token", "refresh_token"}


def test_alembic_ini_contains_no_database_url() -> None:
    assert "postgresql" not in (BACKEND_DIR / "alembic.ini").read_text(encoding="utf-8")
