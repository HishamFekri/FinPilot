"""The database enforces the ledger rules itself (FINANCIAL_RULES.md §4.3, §17: INV-01 to INV-03).

These tests write raw SQL, bypassing the application, to prove a bug in Python can't store bad
money. Test database only.
"""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.db


@pytest.fixture
def db(auth_engine: Engine, auth_clean: None) -> Iterator[Engine]:
    yield auth_engine


def _user(c: Connection, email: str = "owner@example.com") -> uuid.UUID:
    return c.execute(
        text("INSERT INTO users (email, display_name) VALUES (:e, 'U') RETURNING id"), {"e": email}
    ).scalar_one()


def _account(
    c: Connection, user: uuid.UUID, currency: str = "TRY", name: str = "Cash"
) -> uuid.UUID:
    return c.execute(
        text(
            "INSERT INTO financial_accounts (user_id, name, type, currency, opened_on) "
            "VALUES (:u, :n, 'cash', :c, '2026-01-01') RETURNING id"
        ),
        {"u": user, "n": name, "c": currency},
    ).scalar_one()


def _ledger(
    c: Connection, user: uuid.UUID, kind: str, currency: str, account: uuid.UUID | None = None
) -> uuid.UUID:
    return c.execute(
        text(
            "INSERT INTO ledger_accounts (user_id, kind, currency, financial_account_id) "
            "VALUES (:u, :k, :c, :a) RETURNING id"
        ),
        {"u": user, "k": kind, "c": currency, "a": account},
    ).scalar_one()


def _transaction(
    c: Connection, user: uuid.UUID, account: uuid.UUID, currency: str = "TRY"
) -> uuid.UUID:
    return c.execute(
        text(
            "INSERT INTO transactions (user_id, type, account_id, amount_minor, direction, "
            "currency, occurred_at, effective_date) VALUES (:u, 'opening_balance', :a, 100, "
            "'increase', :c, now(), '2026-01-01') RETURNING id"
        ),
        {"u": user, "a": account, "c": currency},
    ).scalar_one()


def _entry(c: Connection, user: uuid.UUID, transaction: uuid.UUID) -> uuid.UUID:
    return c.execute(
        text(
            "INSERT INTO journal_entries (user_id, transaction_id, transaction_type, "
            "effective_date) VALUES (:u, :t, 'opening_balance', '2026-01-01') RETURNING id"
        ),
        {"u": user, "t": transaction},
    ).scalar_one()


def _line(
    c: Connection, entry: uuid.UUID, user: uuid.UUID, ledger: uuid.UUID, currency: str, amount: int
) -> None:
    c.execute(
        text(
            "INSERT INTO journal_lines (entry_id, user_id, effective_date, ledger_account_id, "
            "currency, amount_minor) VALUES (:e, :u, '2026-01-01', :l, :c, :a)"
        ),
        {"e": entry, "u": user, "l": ledger, "c": currency, "a": amount},
    )


class Books:
    """One user with a TRY cash account, its asset ledger account, and Equity:Opening in TRY."""

    def __init__(self, c: Connection) -> None:
        self.user = _user(c)
        self.account = _account(c, self.user)
        self.asset = _ledger(c, self.user, "asset", "TRY", self.account)
        self.equity = _ledger(c, self.user, "equity_opening", "TRY")
        self.transaction = _transaction(c, self.user, self.account)


def _setup(db: Engine) -> Books:
    with db.begin() as c:
        return Books(c)


def test_currencies_are_seeded_with_iso_exponents(db: Engine) -> None:
    with db.connect() as c:
        rows: dict[str, int] = dict(c.execute(text("SELECT code, exponent FROM currencies")).all())
        inactive: set[str] = set(
            c.execute(text("SELECT code FROM currencies WHERE NOT is_active")).scalars()
        )
    assert rows["TRY"] == rows["EUR"] == rows["USD"] == rows["SAR"] == 2
    assert rows["JPY"] == rows["KRW"] == 0
    assert rows["KWD"] == rows["BHD"] == rows["JOD"] == 3
    assert inactive == {"BGN"}
    assert len(rows) > 150


def test_a_balanced_entry_is_accepted(db: Engine) -> None:
    b = _setup(db)
    with db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, b.asset, "TRY", 12550)
        _line(c, entry, b.user, b.equity, "TRY", -12550)
    with db.connect() as c:
        total: int = c.execute(
            text("SELECT sum(amount_minor) FROM journal_lines WHERE ledger_account_id = :l"),
            {"l": b.asset},
        ).scalar_one()
    assert total == 12550


@pytest.mark.parametrize("credit", [-12549, -12551, 0])
def test_an_unbalanced_entry_fails_at_commit(db: Engine, credit: int) -> None:
    """INV-01: lines sum to zero per currency. Checked when the transaction commits."""
    b = _setup(db)
    with pytest.raises((IntegrityError, DBAPIError)), db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, b.asset, "TRY", 12550)
        if credit:
            _line(c, entry, b.user, b.equity, "TRY", credit)
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM journal_entries")).scalar_one() == 0


def test_an_entry_without_lines_fails_at_commit(db: Engine) -> None:
    b = _setup(db)
    with pytest.raises(DBAPIError, match="at least two"), db.begin() as c:
        _entry(c, b.user, b.transaction)


def test_a_zero_line_is_refused(db: Engine) -> None:
    b = _setup(db)
    with pytest.raises(IntegrityError, match="amount_range"), db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, b.asset, "TRY", 0)


def test_cross_currency_entries_balance_per_currency(db: Engine) -> None:
    """3,500.00 TRY out, 100.00 EUR in, through FX clearing (FINANCIAL_RULES.md §4.5)."""
    b = _setup(db)
    with db.begin() as c:
        eur_account = _account(c, b.user, "EUR", "Savings")
        eur_asset = _ledger(c, b.user, "asset", "EUR", eur_account)
        fx_eur = _ledger(c, b.user, "fx_clearing", "EUR")
        fx_try = _ledger(c, b.user, "fx_clearing", "TRY")
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, eur_asset, "EUR", 10000)
        _line(c, entry, b.user, fx_eur, "EUR", -10000)
        _line(c, entry, b.user, fx_try, "TRY", 350000)
        _line(c, entry, b.user, b.asset, "TRY", -350000)
    # Mixing currencies in one balance is refused, even if the numbers cancel.
    with pytest.raises(DBAPIError, match="does not balance"), db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, eur_asset, "EUR", 10000)
        _line(c, entry, b.user, b.asset, "TRY", -10000)


def test_a_line_in_another_currency_than_its_account_is_refused(db: Engine) -> None:
    """INV-02 (L3): the composite key carries the currency."""
    b = _setup(db)
    with pytest.raises(IntegrityError), db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, b.asset, "EUR", 100)


def test_posting_to_another_users_ledger_account_is_impossible(db: Engine) -> None:
    """INV-02 (L4): the line's user must own both the entry and the ledger account."""
    b = _setup(db)
    with db.begin() as c:
        stranger = _user(c, "stranger@example.com")
        stranger_account = _account(c, stranger)
        stranger_asset = _ledger(c, stranger, "asset", "TRY", stranger_account)
    with pytest.raises(IntegrityError), db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, stranger_asset, "TRY", 100)
    with pytest.raises(IntegrityError), db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, stranger, stranger_asset, "TRY", 100)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE journal_lines SET amount_minor = amount_minor * 2",
        "DELETE FROM journal_lines",
        "UPDATE journal_entries SET effective_date = '2025-01-01'",
        "DELETE FROM journal_entries",
        "TRUNCATE journal_lines",
        "TRUNCATE journal_entries CASCADE",
    ],
)
def test_the_journal_is_append_only(db: Engine, statement: str) -> None:
    """INV-03 (L5): corrections are reversals, never edits."""
    b = _setup(db)
    with db.begin() as c:
        entry = _entry(c, b.user, b.transaction)
        _line(c, entry, b.user, b.asset, "TRY", 100)
        _line(c, entry, b.user, b.equity, "TRY", -100)
    with pytest.raises(DBAPIError, match="append-only"), db.begin() as c:
        c.execute(text(statement))


def test_an_account_currency_never_changes(db: Engine) -> None:
    b = _setup(db)
    with pytest.raises(DBAPIError, match="never change"), db.begin() as c:
        c.execute(
            text("UPDATE financial_accounts SET currency = 'EUR' WHERE id = :a"), {"a": b.account}
        )


def test_a_transaction_type_never_changes(db: Engine) -> None:
    _setup(db)
    with pytest.raises(DBAPIError, match="never change"), db.begin() as c:
        c.execute(
            text("UPDATE transactions SET type = 'opening_balance', user_id = gen_random_uuid()")
        )


def test_ledger_account_shape_is_enforced(db: Engine) -> None:
    b = _setup(db)
    # An asset account needs its financial account; other kinds must not have one.
    with pytest.raises(IntegrityError, match="asset_has_account"), db.begin() as c:
        _ledger(c, b.user, "asset", "TRY")
    with pytest.raises(IntegrityError, match="asset_has_account"), db.begin() as c:
        _ledger(c, b.user, "income", "TRY", b.account)
    # An asset ledger account is in its financial account's currency.
    with pytest.raises(IntegrityError), db.begin() as c:
        other = _account(c, b.user, "TRY", "Other")
        _ledger(c, b.user, "asset", "EUR", other)
    # One shared account per user, kind, and currency.
    with pytest.raises(IntegrityError, match="user_kind_currency"), db.begin() as c:
        _ledger(c, b.user, "equity_opening", "TRY")


def test_transactions_are_in_their_own_accounts_currency_and_capped(db: Engine) -> None:
    b = _setup(db)
    with pytest.raises(IntegrityError), db.begin() as c:
        _transaction(c, b.user, b.account, "EUR")
    with pytest.raises(IntegrityError, match="amount_range"), db.begin() as c:
        c.execute(
            text("UPDATE transactions SET amount_minor = :big WHERE id = :t"),
            {"big": 10**15 + 1, "t": b.transaction},
        )
    with pytest.raises(IntegrityError, match="one_opening_balance"), db.begin() as c:
        _transaction(c, b.user, b.account)


@pytest.mark.parametrize("name", ["", "  Cash", "Cash\u202e", "Ca\x00sh", "x" * 101])
def test_account_names_are_clean(db: Engine, name: str) -> None:
    with db.begin() as c:
        user = _user(c)
    with pytest.raises((IntegrityError, DBAPIError)), db.begin() as c:
        _account(c, user, name=name)


def test_active_account_names_are_unique_per_user_ignoring_case(db: Engine) -> None:
    b = _setup(db)
    with pytest.raises(IntegrityError, match="active_name"), db.begin() as c:
        _account(c, b.user, name="CASH")
    with db.begin() as c:
        c.execute(
            text("UPDATE financial_accounts SET status = 'archived' WHERE id = :a"),
            {"a": b.account},
        )
        _account(c, b.user, name="cash")  # an archived name can be reused
        other = _user(c, "other@example.com")
        _account(c, other, name="Cash")  # and another user's names are their own


def _transfer_row(
    c: Connection, b: Books, destination: uuid.UUID, *, sent: int, received: int, currency: str
) -> None:
    c.execute(
        text(
            "INSERT INTO transactions (user_id, type, account_id, amount_minor, currency, "
            "counter_account_id, counter_amount_minor, counter_currency, occurred_at, "
            "effective_date) VALUES (:u, 'transfer', :a, :s, 'TRY', :d, :r, :c, now(), "
            "'2026-01-02')"
        ),
        {"u": b.user, "a": b.account, "s": sent, "d": destination, "r": received, "c": currency},
    )


def test_a_same_currency_transfer_cant_create_or_destroy_money(db: Engine) -> None:
    b = _setup(db)
    with db.begin() as c:
        cash = _account(c, b.user, "TRY", "Cash 2")
        _transfer_row(c, b, cash, sent=500, received=500, currency="TRY")
    with pytest.raises(IntegrityError, match="same_currency_same_amount"), db.begin() as c:
        _transfer_row(c, b, cash, sent=500, received=499, currency="TRY")


def test_a_transfer_goes_to_another_of_the_same_users_accounts(db: Engine) -> None:
    b = _setup(db)
    with db.begin() as c:
        stranger = _user(c, "stranger@example.com")
        theirs = _account(c, stranger)
        euro = _account(c, b.user, "EUR", "Euro")
    # Another user's account: the composite key refuses it.
    with pytest.raises(IntegrityError), db.begin() as c:
        _transfer_row(c, b, theirs, sent=100, received=100, currency="TRY")
    # Its own source account: refused.
    with pytest.raises(IntegrityError, match="counter_is_another_account"), db.begin() as c:
        _transfer_row(c, b, b.account, sent=100, received=100, currency="TRY")
    # A destination in a currency that isn't the destination's: refused.
    with pytest.raises(IntegrityError), db.begin() as c:
        _transfer_row(c, b, euro, sent=100, received=3, currency="TRY")
    # Correct: EUR into the EUR account.
    with db.begin() as c:
        _transfer_row(c, b, euro, sent=350000, received=10000, currency="EUR")
