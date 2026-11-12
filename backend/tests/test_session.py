"""Transaction handling for the request-scoped session, without a database server."""

from unittest.mock import MagicMock

import pytest

from app.db import session as session_module
from app.db.base import Base
from app.db.session import build_engine, get_db
from tests.conftest import UNREACHABLE_URL, make_settings


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(session_module, "_session_factory", lambda: lambda: fake)
    return fake


def test_commits_and_closes_on_success(fake_session: MagicMock) -> None:
    dependency = get_db()
    assert next(dependency) is fake_session
    with pytest.raises(StopIteration):
        next(dependency)
    fake_session.commit.assert_called_once()
    fake_session.rollback.assert_not_called()
    fake_session.close.assert_called_once()


def test_rolls_back_and_closes_on_error(fake_session: MagicMock) -> None:
    dependency = get_db()
    next(dependency)
    with pytest.raises(ValueError, match="boom"):
        dependency.throw(ValueError("boom"))
    fake_session.commit.assert_not_called()
    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()


def test_engine_creation_does_not_connect() -> None:
    # The URL's port refuses connections, so this would raise if it connected.
    engine = build_engine(make_settings(database_url=UNREACHABLE_URL))
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"


def test_metadata_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"


def test_engine_hides_bound_values_from_database_errors() -> None:
    # Otherwise a failed insert would repeat hashes and emails in its error message.
    assert build_engine(make_settings()).hide_parameters is True
