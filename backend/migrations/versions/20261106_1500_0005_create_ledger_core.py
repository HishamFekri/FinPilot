"""Create the ledger core: currencies, financial accounts, ledger accounts, transactions, journal.

A3.1. Creates new objects only; nothing existing is altered. Rules: docs/FINANCIAL_RULES.md §2,
§4 and docs/DATABASE.md §3.1, §3.3.

- Money is BIGINT minor units, capped at 10^15 (M1, M6). Exponents live in `currencies` (M3).
- `financial_accounts` has no balance column. Balances are always derived from journal lines.
- Composite foreign keys make it impossible to post to another user's account, or in a currency
  that isn't the account's (L3, L4).
- Journal entries and lines can't be updated or deleted (L5). A deferred constraint trigger checks
  at commit that every entry has at least two lines and sums to zero per currency (L1, L2).
- `transactions` starts with the `opening_balance` type only. Later steps widen the type check and
  add their columns (categories, transfers) additively.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15

# ISO 4217 codes in circulation, and their minor-unit exponents (default 2). Fund and precious-metal
# codes are left out. Names are English fallbacks; the web app shows names in the reader's language.
EXPONENT_0 = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV",
    "XAF", "XOF", "XPF",
}  # fmt: skip
EXPONENT_3 = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
# Bulgaria adopted the euro on 1 January 2026: kept for history, closed to new records.
INACTIVE = {"BGN"}
CURRENCIES = {
    "AED": "UAE Dirham", "AFN": "Afghani", "ALL": "Lek", "AMD": "Armenian Dram",
    "AOA": "Kwanza", "ARS": "Argentine Peso", "AUD": "Australian Dollar", "AWG": "Aruban Florin",
    "AZN": "Azerbaijan Manat", "BAM": "Convertible Mark", "BBD": "Barbados Dollar", "BDT": "Taka",
    "BGN": "Bulgarian Lev", "BHD": "Bahraini Dinar", "BIF": "Burundi Franc",
    "BMD": "Bermudian Dollar", "BND": "Brunei Dollar", "BOB": "Boliviano", "BRL": "Brazilian Real",
    "BSD": "Bahamian Dollar", "BTN": "Ngultrum", "BWP": "Pula", "BYN": "Belarusian Ruble",
    "BZD": "Belize Dollar", "CAD": "Canadian Dollar", "CDF": "Congolese Franc",
    "CHF": "Swiss Franc", "CLP": "Chilean Peso", "CNY": "Yuan Renminbi", "COP": "Colombian Peso",
    "CRC": "Costa Rican Colon", "CUP": "Cuban Peso", "CVE": "Cabo Verde Escudo",
    "CZK": "Czech Koruna", "DJF": "Djibouti Franc", "DKK": "Danish Krone",
    "DOP": "Dominican Peso", "DZD": "Algerian Dinar", "EGP": "Egyptian Pound", "ERN": "Nakfa",
    "ETB": "Ethiopian Birr", "EUR": "Euro", "FJD": "Fiji Dollar",
    "FKP": "Falkland Islands Pound", "GBP": "Pound Sterling", "GEL": "Lari", "GHS": "Ghana Cedi",
    "GIP": "Gibraltar Pound", "GMD": "Dalasi", "GNF": "Guinean Franc", "GTQ": "Quetzal",
    "GYD": "Guyana Dollar", "HKD": "Hong Kong Dollar", "HNL": "Lempira", "HTG": "Gourde",
    "HUF": "Forint", "IDR": "Rupiah", "ILS": "New Israeli Sheqel", "INR": "Indian Rupee",
    "IQD": "Iraqi Dinar", "IRR": "Iranian Rial", "ISK": "Iceland Krona",
    "JMD": "Jamaican Dollar", "JOD": "Jordanian Dinar", "JPY": "Yen", "KES": "Kenyan Shilling",
    "KGS": "Som", "KHR": "Riel", "KMF": "Comorian Franc", "KPW": "North Korean Won",
    "KRW": "Won", "KWD": "Kuwaiti Dinar", "KYD": "Cayman Islands Dollar", "KZT": "Tenge",
    "LAK": "Lao Kip", "LBP": "Lebanese Pound", "LKR": "Sri Lanka Rupee",
    "LRD": "Liberian Dollar", "LSL": "Loti", "LYD": "Libyan Dinar", "MAD": "Moroccan Dirham",
    "MDL": "Moldovan Leu", "MGA": "Malagasy Ariary", "MKD": "Denar", "MMK": "Kyat",
    "MNT": "Tugrik", "MOP": "Pataca", "MRU": "Ouguiya", "MUR": "Mauritius Rupee",
    "MVR": "Rufiyaa", "MWK": "Malawi Kwacha", "MXN": "Mexican Peso",
    "MYR": "Malaysian Ringgit", "MZN": "Mozambique Metical", "NAD": "Namibia Dollar",
    "NGN": "Naira", "NIO": "Cordoba Oro", "NOK": "Norwegian Krone", "NPR": "Nepalese Rupee",
    "NZD": "New Zealand Dollar", "OMR": "Rial Omani", "PAB": "Balboa", "PEN": "Sol",
    "PGK": "Kina", "PHP": "Philippine Peso", "PKR": "Pakistan Rupee", "PLN": "Zloty",
    "PYG": "Guarani", "QAR": "Qatari Rial", "RON": "Romanian Leu", "RSD": "Serbian Dinar",
    "RUB": "Russian Ruble", "RWF": "Rwanda Franc", "SAR": "Saudi Riyal",
    "SBD": "Solomon Islands Dollar", "SCR": "Seychelles Rupee", "SDG": "Sudanese Pound",
    "SEK": "Swedish Krona", "SGD": "Singapore Dollar", "SHP": "Saint Helena Pound",
    "SLE": "Leone", "SOS": "Somali Shilling", "SRD": "Surinam Dollar",
    "SSP": "South Sudanese Pound", "STN": "Dobra", "SVC": "El Salvador Colon",
    "SYP": "Syrian Pound", "SZL": "Lilangeni", "THB": "Baht", "TJS": "Somoni",
    "TMT": "Turkmenistan New Manat", "TND": "Tunisian Dinar", "TOP": "Pa'anga",
    "TRY": "Turkish Lira", "TTD": "Trinidad and Tobago Dollar", "TWD": "New Taiwan Dollar",
    "TZS": "Tanzanian Shilling", "UAH": "Hryvnia", "UGX": "Uganda Shilling", "USD": "US Dollar",
    "UYU": "Peso Uruguayo", "UZS": "Uzbekistan Sum", "VED": "Bolivar Soberano",
    "VES": "Bolivar Soberano", "VND": "Dong", "VUV": "Vatu", "WST": "Tala",
    "XAF": "CFA Franc BEAC", "XCD": "East Caribbean Dollar", "XCG": "Caribbean Guilder",
    "XOF": "CFA Franc BCEAO", "XPF": "CFP Franc", "YER": "Yemeni Rial", "ZAR": "Rand",
    "ZMW": "Zambian Kwacha", "ZWG": "Zimbabwe Gold",
}  # fmt: skip

ACCOUNT_TYPES = ("cash", "bank", "savings", "wallet", "other")
LEDGER_KINDS = (
    "asset", "income", "expense", "equity_opening", "equity_adjustment", "fx_clearing",
    "trip_clearing",
)  # fmt: skip
TRANSACTION_TYPES = ("opening_balance",)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# A name a person types: trimmed, 1-100 characters, and no control or bidirectional-formatting
# characters, which could reorder how a name or amount reads (SECURITY.md §6).
def _clean_name(column: str, max_length: int) -> str:
    return (
        f"{column} = btrim({column}) AND char_length({column}) BETWEEN 1 AND {max_length} "
        rf"AND {column} !~ '[\x01-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]'"
    )


IMMUTABLE_ACCOUNT_FIELDS = """
CREATE FUNCTION finpilot_financial_account_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.currency <> OLD.currency OR NEW.user_id <> OLD.user_id THEN
        RAISE EXCEPTION 'an account''s currency and owner never change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$
"""
IMMUTABLE_TRANSACTION_FIELDS = """
CREATE FUNCTION finpilot_transaction_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.type <> OLD.type OR NEW.user_id <> OLD.user_id THEN
        RAISE EXCEPTION 'a transaction''s type and owner never change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$
"""
BLOCK_JOURNAL_CHANGE = """
CREATE FUNCTION finpilot_block_journal_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: post a reversal instead', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$
"""
# Runs at commit (deferred), once per inserted entry or line, so a multi-line entry is checked
# only when complete. Reads the whole entry each time; entries have a handful of lines.
CHECK_ENTRY_BALANCED = """
CREATE FUNCTION finpilot_check_entry_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
    line_count integer;
    unbalanced text;
BEGIN
    -- Separate branches: PL/pgSQL resolves every field a CASE names, even in the branch not taken.
    IF TG_TABLE_NAME = 'journal_entries' THEN
        target := NEW.id;
    ELSE
        target := NEW.entry_id;
    END IF;
    SELECT count(*) INTO line_count FROM journal_lines WHERE entry_id = target;
    IF line_count < 2 THEN
        RAISE EXCEPTION 'journal entry % has % line(s); at least two are required',
            target, line_count USING ERRCODE = 'check_violation';
    END IF;
    SELECT string_agg(currency, ', ') INTO unbalanced FROM (
        SELECT currency FROM journal_lines WHERE entry_id = target
        GROUP BY currency HAVING sum(amount_minor) <> 0
    ) AS per_currency;
    IF unbalanced IS NOT NULL THEN
        RAISE EXCEPTION 'journal entry % does not balance in %', target, unbalanced
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    # --- Reference
    currencies = op.create_table(
        "currencies",
        sa.Column("code", sa.CHAR(3), nullable=False),
        sa.Column("exponent", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z]{3}$'", name=op.f("ck_currencies_code_format")),
        sa.CheckConstraint("exponent BETWEEN 0 AND 4", name=op.f("ck_currencies_exponent_range")),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_currencies")),
    )
    op.bulk_insert(
        currencies,
        [
            {
                "code": code,
                "exponent": 0 if code in EXPONENT_0 else 3 if code in EXPONENT_3 else 2,
                "name": name,
                "is_active": code not in INACTIVE,
            }
            for code, name in sorted(CURRENCIES.items())
        ],
    )

    # --- Financial accounts: what the person sees. No balance column, ever.
    op.create_table(
        "financial_accounts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(_in("type", ACCOUNT_TYPES), name=op.f("ck_financial_accounts_type_allowed")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_financial_accounts_status_allowed")),
        sa.CheckConstraint(_clean_name("name", 100), name=op.f("ck_financial_accounts_name_clean")),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 500",
            name=op.f("ck_financial_accounts_description_length"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_financial_accounts_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_financial_accounts_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_financial_accounts_currency_currencies"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_accounts")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_financial_accounts_id_user_id")),
        sa.UniqueConstraint("id", "user_id", "currency", name=op.f("uq_financial_accounts_id_user_id_currency")),
    )  # fmt: skip
    op.create_index(
        "uq_financial_accounts_active_name",
        "financial_accounts",
        ["user_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_financial_accounts_user_id_status"), "financial_accounts", ["user_id", "status"]
    )

    # --- Ledger accounts: the internal chart of accounts (FINANCIAL_RULES.md §4.2).
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("financial_account_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("kind", LEDGER_KINDS), name=op.f("ck_ledger_accounts_kind_allowed")),
        sa.CheckConstraint(
            "(kind = 'asset') = (financial_account_id IS NOT NULL)",
            name=op.f("ck_ledger_accounts_asset_has_account"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_ledger_accounts_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_ledger_accounts_currency_currencies"), ondelete="RESTRICT"),
        # The asset ledger account has its financial account's owner and currency, by construction.
        sa.ForeignKeyConstraint(
            ["financial_account_id", "user_id", "currency"],
            ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"],
            name=op.f("fk_ledger_accounts_financial_account_id_user_id_currency_financial_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_accounts")),
        sa.UniqueConstraint("id", "user_id", "currency", name=op.f("uq_ledger_accounts_id_user_id_currency")),
    )  # fmt: skip
    op.create_index(
        "uq_ledger_accounts_financial_account",
        "ledger_accounts",
        ["financial_account_id"],
        unique=True,
        postgresql_where=sa.text("financial_account_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ledger_accounts_user_kind_currency",
        "ledger_accounts",
        ["user_id", "kind", "currency"],
        unique=True,
        postgresql_where=sa.text("financial_account_id IS NULL"),
    )

    # --- Transactions: the user-facing document. Its journal entries are the financial effect.
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="posted", nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_via", sa.Text(), server_default="form", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(_in("type", TRANSACTION_TYPES), name=op.f("ck_transactions_type_allowed")),
        sa.CheckConstraint("status IN ('posted', 'reversed')", name=op.f("ck_transactions_status_allowed")),
        sa.CheckConstraint(
            f"amount_minor > 0 AND amount_minor <= {MAX_MINOR}", name=op.f("ck_transactions_amount_range")
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('increase', 'decrease')",
            name=op.f("ck_transactions_direction_allowed"),
        ),
        sa.CheckConstraint(
            "(type IN ('adjustment', 'opening_balance')) = (direction IS NOT NULL)",
            name=op.f("ck_transactions_direction_when_needed"),
        ),
        sa.CheckConstraint(
            "created_via IN ('form', 'quick_add', 'link', 'recurring')",
            name=op.f("ck_transactions_created_via_allowed"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 200",
            name=op.f("ck_transactions_description_length"),
        ),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 2000", name=op.f("ck_transactions_notes_length")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_transactions_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_transactions_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_transactions_currency_currencies"), ondelete="RESTRICT"),
        # Own account only, and always in that account's currency (FINANCIAL_RULES.md §4.6).
        sa.ForeignKeyConstraint(
            ["account_id", "user_id", "currency"],
            ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"],
            name=op.f("fk_transactions_account_id_user_id_currency_financial_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_transactions_id_user_id")),
    )  # fmt: skip
    op.create_index(
        "ix_transactions_history",
        "transactions",
        ["user_id", sa.text("effective_date DESC"), sa.text("id DESC")],
    )
    op.create_index(
        op.f("ix_transactions_account_id_effective_date"),
        "transactions",
        ["account_id", "effective_date"],
    )
    # One live opening balance per account.
    op.create_index(
        "uq_transactions_one_opening_balance",
        "transactions",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("type = 'opening_balance' AND status = 'posted'"),
    )

    # --- Journal: immutable financial events.
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_type", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("posted_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("reverses_entry_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_journal_entries_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name=op.f("fk_journal_entries_transaction_id_user_id_transactions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_entry_id"], ["journal_entries.id"],
            name=op.f("fk_journal_entries_reverses_entry_id_journal_entries"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entries")),
        sa.UniqueConstraint("id", "user_id", "effective_date", name=op.f("uq_journal_entries_id_user_id_effective_date")),
        # An entry is reversed at most once.
        sa.UniqueConstraint("reverses_entry_id", name=op.f("uq_journal_entries_reverses_entry_id")),
    )  # fmt: skip
    op.create_index(
        op.f("ix_journal_entries_user_id_effective_date"),
        "journal_entries",
        ["user_id", "effective_date"],
    )
    op.create_index(
        op.f("ix_journal_entries_transaction_id"), "journal_entries", ["transaction_id"]
    )

    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("ledger_account_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            f"amount_minor <> 0 AND abs(amount_minor) <= {MAX_MINOR}",
            name=op.f("ck_journal_lines_amount_range"),
        ),
        # The line belongs to its entry's user and date (L4)...
        sa.ForeignKeyConstraint(
            ["entry_id", "user_id", "effective_date"],
            ["journal_entries.id", "journal_entries.user_id", "journal_entries.effective_date"],
            name=op.f("fk_journal_lines_entry_id_user_id_effective_date_journal_entries"),
            ondelete="RESTRICT",
        ),
        # ...and posts to that user's ledger account, in that account's currency (L3, L4).
        sa.ForeignKeyConstraint(
            ["ledger_account_id", "user_id", "currency"],
            ["ledger_accounts.id", "ledger_accounts.user_id", "ledger_accounts.currency"],
            name=op.f("fk_journal_lines_ledger_account_id_user_id_currency_ledger_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_lines")),
    )
    op.create_index(
        "ix_journal_lines_balance",
        "journal_lines",
        ["ledger_account_id", "effective_date"],
        postgresql_include=["amount_minor"],
    )
    op.create_index(op.f("ix_journal_lines_entry_id"), "journal_lines", ["entry_id"])

    # --- Idempotency (FINANCIAL_RULES.md §14.1): the key is stored in the same transaction as
    # the operation, so a failure rolls both back and a retry runs cleanly.
    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("octet_length(request_hash) = 32", name=op.f("ck_idempotency_keys_request_hash_sha256")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_idempotency_keys_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_idempotency_keys")),
    )  # fmt: skip
    op.create_index(op.f("ix_idempotency_keys_created_at"), "idempotency_keys", ["created_at"])

    # --- Triggers
    for table in ("financial_accounts", "transactions"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
        )
    op.execute(IMMUTABLE_ACCOUNT_FIELDS)
    op.execute(
        "CREATE TRIGGER trg_financial_accounts_immutable BEFORE UPDATE ON financial_accounts "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_financial_account_immutable()"
    )
    op.execute(IMMUTABLE_TRANSACTION_FIELDS)
    op.execute(
        "CREATE TRIGGER trg_transactions_immutable BEFORE UPDATE ON transactions "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_transaction_immutable()"
    )
    op.execute(BLOCK_JOURNAL_CHANGE)
    op.execute(CHECK_ENTRY_BALANCED)
    for table in ("journal_entries", "journal_lines"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION finpilot_block_journal_change()"
        )
        # TRUNCATE bypasses row triggers, so it's blocked too.
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION finpilot_block_journal_change()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_balanced AFTER INSERT ON {table} "
            "DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION finpilot_check_entry_balanced()"
        )


def downgrade() -> None:
    for table in ("journal_entries", "journal_lines"):
        op.execute(f"DROP TRIGGER trg_{table}_balanced ON {table}")
        op.execute(f"DROP TRIGGER trg_{table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP TRIGGER trg_transactions_immutable ON transactions")
    op.execute("DROP TRIGGER trg_financial_accounts_immutable ON financial_accounts")
    for table in ("financial_accounts", "transactions"):
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.execute("DROP FUNCTION finpilot_check_entry_balanced()")
    op.execute("DROP FUNCTION finpilot_block_journal_change()")
    op.execute("DROP FUNCTION finpilot_transaction_immutable()")
    op.execute("DROP FUNCTION finpilot_financial_account_immutable()")
    op.drop_table("idempotency_keys")
    op.drop_table("journal_lines")
    op.drop_table("journal_entries")
    op.drop_table("transactions")
    op.drop_table("ledger_accounts")
    op.drop_table("financial_accounts")
    op.drop_table("currencies")
