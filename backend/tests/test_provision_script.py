"""The provisioning script's SQL and secret handling, with nothing sent to any server."""

import base64
import hashlib
import hmac
import re

from scripts.provision_local_db import ENVIRONMENTS, build_sql, env_contents, scram_verifier

SQL = build_sql({"dev": "VERIFIER_DEV", "test": "VERIFIER_TEST"})


def test_scram_verifier_matches_rfc_7677_test_vector() -> None:
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = scram_verifier("pencil", salt)
    match = re.fullmatch(r"SCRAM-SHA-256\$4096:([^$]+)\$([^:]+):(.+)", verifier)
    assert match is not None
    assert match.group(1) == "W22ZaJ0SNY7soEsUEjb6gQ=="
    stored_key = base64.b64decode(match.group(2))
    server_key = base64.b64decode(match.group(3))

    auth_message = (
        b"n=user,r=rOprNGfwEbeRWgbNEkqO,"
        b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
        b"s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
        b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    )
    # The RFC's server signature proves the server key.
    assert hmac.digest(server_key, auth_message, "sha256") == base64.b64decode(
        "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    )
    # The RFC's client proof proves the stored key.
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    signature = hmac.digest(stored_key, auth_message, "sha256")
    client_key = bytes(a ^ b for a, b in zip(proof, signature, strict=True))
    assert hashlib.sha256(client_key).digest() == stored_key


def test_salt_is_random_per_verifier() -> None:
    assert scram_verifier("same") != scram_verifier("same")


def test_sql_only_creates_the_approved_objects() -> None:
    assert re.findall(r"^CREATE DATABASE (\w+)", SQL, re.M) == ["finpilot_dev", "finpilot_test"]
    assert re.findall(r"^CREATE ROLE (\w+)", SQL, re.M) == [
        "finpilot_dev_user",
        "finpilot_test_user",
    ]
    assert re.findall(r"^REVOKE ALL ON DATABASE (\w+)", SQL, re.M) == [
        "finpilot_dev",
        "finpilot_test",
    ]


def test_sql_contains_nothing_that_changes_existing_objects() -> None:
    statements = re.sub(r"^--.*$", "", SQL, flags=re.M).upper()
    for forbidden in (
        "DROP ",
        "ALTER ",
        "TRUNCATE",
        "DELETE ",
        "UPDATE ",
        "INSERT ",
        "GRANT ",
        "ALTER SYSTEM",
        "PG_HBA",
        "POSTGRESQL.CONF",
    ):
        assert forbidden not in statements, forbidden


def test_roles_have_no_server_wide_privileges() -> None:
    for attribute in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOBYPASSRLS",
        "NOINHERIT",
    ):
        assert SQL.count(attribute) == 2, attribute


def test_conflict_check_comes_before_any_creation() -> None:
    assert SQL.index("RAISE EXCEPTION") < SQL.index("CREATE ROLE")
    assert SQL.index("ON_ERROR_STOP on") < SQL.index("RAISE EXCEPTION")


def test_each_role_is_paired_with_only_its_own_database() -> None:
    env = env_contents({"dev": "DEVPW", "test": "TESTPW"}, 5432)
    assert "finpilot_dev_user:DEVPW@127.0.0.1:5432/finpilot_dev\n" in env
    assert "finpilot_test_user:TESTPW@127.0.0.1:5432/finpilot_test\n" in env
    assert set(ENVIRONMENTS) == {"dev", "test"}
