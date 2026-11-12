"""Rotate the FinPilot DEVELOPMENT database role's password (finpilot_dev_user only).

Uses the role's own right to change its password (PostgreSQL lets any role ALTER its own
password), so no administrator password is needed. The new password is generated here, sent to
the server only as a SCRAM-SHA-256 verifier (so the plaintext never reaches the server or its
logs), written only to backend/.env (DATABASE_URL line only), and never printed.

    uv run python -m scripts.rotate_dev_password

Safety: refuses unless DATABASE_URL points at database finpilot_dev, role finpilot_dev_user, on
localhost, and the server confirms the same. It never touches TEST_DATABASE_URL, other roles, or
other databases. The new .env is written to a temporary file first; if the role change fails the
temporary file is removed, and if replacing .env fails afterwards the temporary file keeps the new
value, so the password can never be lost. Output names steps only, never a credential.
"""

import os
import re
import secrets
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from scripts.provision_local_db import ENVIRONMENTS, scram_verifier

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"
DATABASE, ROLE = ENVIRONMENTS["dev"]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
VERIFIER = re.compile(r"^SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")


def _identity(url: str) -> tuple[str, str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT current_database(), current_user")).one()
            return str(row[0]), str(row[1])
    finally:
        engine.dispose()


def _connects(url: str) -> bool:
    try:
        return _identity(url) == (DATABASE, ROLE)
    except Exception:  # only success matters; never echo the error (it may hold the URL)
        return False


def main() -> int:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if line.startswith("DATABASE_URL=")), None)
    if index is None:
        print("stopped: DATABASE_URL isn't in backend/.env")
        return 1
    old_url = lines[index].split("=", 1)[1].strip()
    try:
        parsed = make_url(old_url)
    except Exception:
        print("stopped: DATABASE_URL couldn't be read")
        return 1
    if parsed.database != DATABASE or parsed.username != ROLE or parsed.host not in LOCAL_HOSTS:
        print("stopped: DATABASE_URL isn't the local FinPilot development database and role")
        return 1
    try:
        if _identity(old_url) != (DATABASE, ROLE):
            print("stopped: the server reports a different database or role")
            return 1
    except Exception:
        print("stopped: couldn't connect with the current DATABASE_URL")
        return 1
    print(f"verified: database {DATABASE}, role {ROLE}, local server")

    new_password = secrets.token_urlsafe(32)
    new_url = parsed.set(password=new_password).render_as_string(hide_password=False)
    verifier = scram_verifier(new_password)
    if not VERIFIER.match(verifier):  # pragma: no cover - defensive; only safe characters
        print("stopped: unexpected verifier format")
        return 1

    staged = ENV_FILE.with_name(".env.rotating")
    lines[index] = f"DATABASE_URL={new_url}\n"
    staged.write_text("".join(lines), encoding="utf-8")
    print("staged: new .env written to a temporary file")

    try:
        engine = create_engine(old_url, poolclass=NullPool)
        try:
            with engine.begin() as connection:
                # Only this role; the verifier holds only base64, digits, '$' and ':'.
                # exec_driver_sql: the verifier's colons must not be read as bind parameters.
                connection.exec_driver_sql(f"ALTER ROLE {ROLE} PASSWORD '{verifier}'")
        finally:
            engine.dispose()
    except Exception as error:
        staged.unlink(missing_ok=True)
        print(f"error type: {type(error).__name__}")
        print("stopped: the role's password wasn't changed; backend/.env is unchanged")
        return 1
    print(f"rotated: {ROLE} password changed (sent as a SCRAM verifier)")

    os.replace(staged, ENV_FILE)
    print("updated: backend/.env DATABASE_URL (no other line changed)")

    ok_new = _connects(new_url)
    ok_old = _connects(old_url)
    print(f"check: new credential connects: {ok_new}")
    print(f"check: old credential refused: {not ok_old}")
    return 0 if ok_new and not ok_old else 1


if __name__ == "__main__":
    sys.exit(main())
