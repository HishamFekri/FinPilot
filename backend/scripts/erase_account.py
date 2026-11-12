"""Finish a requested account deletion. Irreversible. Test databases only, for now.

    OWNER_DATABASE_URL=... uv run python -m scripts.erase_account --user-id <uuid> --grace-days 14

Refuses every database not named *_test, with no override. It stays that way until the retention
questions in docs/SECURITY.md §11.5 are decided: how long the grace period is, what is kept, and
for how long. Lifting that is a deliberate code change, not a flag.

Connects as the schema owner, because only the owner can lift the append-only triggers.
"""

import argparse
import os
import sys
import uuid
from datetime import timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.services.account_erasure import ErasureRefusedError, erase


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--user-id", required=True, type=uuid.UUID)
    parser.add_argument("--grace-days", required=True, type=int, help="no default, on purpose")
    args = parser.parse_args()

    url = os.environ.get("OWNER_DATABASE_URL")
    if not url:
        print("OWNER_DATABASE_URL isn't set.")
        return 2
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    with Session(engine) as db:
        name = str(db.scalar(text("SELECT current_database()")))
        if not name.endswith("_test"):
            print(f"Refusing: {name!r} isn't a test database. See SECURITY.md §11.5.")
            return 2
        try:
            erase(db, args.user_id, grace=timedelta(days=args.grace_days))
        except ErasureRefusedError as exc:
            db.rollback()
            print(f"Refused, nothing changed: {exc}")
            return 1
        db.commit()
    print("Erased.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
