"""Who the current request is acting for, told to PostgreSQL for row-level security.

Set with `set_config(..., is_local => true)`, so it ends with the transaction and can't leak to the
next request using the same pooled connection. Routes commit part-way through, so the values are
kept on the Session and applied again at the start of every transaction.

Where RLS isn't applied (development today, see `hardening.py`), these settings do nothing.
"""

import uuid
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

USER_SETTING = "app.user_id"
INVITE_SETTING = "app.invite_token_hash"
_SETTINGS = (USER_SETTING, INVITE_SETTING)


def _apply(target: Any, name: str, value: str) -> None:
    target.execute(select(func.set_config(name, value, True)))


def _remember(db: Session, name: str, value: str) -> None:
    db.info[name] = value
    _apply(db, name, value)


def act_as(db: Session, user_id: uuid.UUID) -> None:
    """Called once the session cookie has been validated, never from anything a client sends."""
    _remember(db, USER_SETTING, str(user_id))


def holding_invitation(db: Session, token_hash: bytes) -> None:
    """Lets someone who holds an invitation see that one invitation and its trip, before joining."""
    _remember(db, INVITE_SETTING, token_hash.hex())


@event.listens_for(Session, "after_begin")
def _reapply(session: Session, _transaction: Any, connection: Any) -> None:
    for name in _SETTINGS:
        value = session.info.get(name)
        if value:
            _apply(connection, name, value)
