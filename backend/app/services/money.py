"""Money as exact integers in a currency's minor unit (FINANCIAL_RULES.md §2).

The API speaks canonical decimal strings: ASCII digits, an optional leading "-", and "." as the
decimal mark, such as "1250.50" (M2). They become integers here, using the currency's ISO 4217
exponent (M3). Nothing is ever rounded: more decimals than the currency has is an error. There is no
float anywhere, and no locale: the web app parses what people type and sends canonical form (M7).
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import MAX_MINOR, Currency

# No "+", no thousands separators, no leading zeros, no exponent notation, no whitespace.
_CANONICAL = re.compile(r"^(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?$")


class InvalidAmountError(ValueError):
    """The text isn't a canonical amount for the currency. The message is for logs only."""


def to_minor(amount: str, exponent: int) -> int:
    """'1250.50' with exponent 2 → 125050. Raises InvalidAmountError rather than rounding."""
    match = _CANONICAL.fullmatch(amount)
    if match is None:
        raise InvalidAmountError("not a canonical decimal amount")
    sign: str = match.group(1)
    whole: str = match.group(2)
    fraction: str = match.group(3) or ""
    if len(fraction) > exponent:
        raise InvalidAmountError(f"more than {exponent} decimal places")
    if len(whole) > 16:
        raise InvalidAmountError("too large")
    minor: int = int(whole) * 10**exponent + (int(fraction.ljust(exponent, "0")) if exponent else 0)
    if sign and minor == 0:
        raise InvalidAmountError("negative zero")
    if minor > MAX_MINOR:
        raise InvalidAmountError("larger than the allowed maximum")
    return -minor if sign else minor


def to_amount(minor: int, exponent: int) -> str:
    """125050 with exponent 2 → '1250.50'. Always shows every decimal the currency has."""
    sign = "-" if minor < 0 else ""
    digits = str(abs(minor)).rjust(exponent + 1, "0")
    if exponent == 0:
        return f"{sign}{digits}"
    return f"{sign}{digits[:-exponent]}.{digits[-exponent:]}"


@dataclass(frozen=True)
class CurrencyInfo:
    code: str
    exponent: int
    is_active: bool


def currency(db: Session, code: str, *, for_new_records: bool = True) -> CurrencyInfo:
    """The currency, or 422 unsupported_currency. Inactive ones are refused for new records."""
    row = db.get(Currency, code)
    if row is None or (for_new_records and not row.is_active):
        raise ApiError(422, "unsupported_currency", "Currency isn't supported", {"currency": code})
    return CurrencyInfo(row.code, row.exponent, row.is_active)


def exponents(db: Session, codes: set[str]) -> dict[str, int]:
    """Exponents for several currencies at once, for formatting responses."""
    if not codes:
        return {}
    rows = db.execute(select(Currency.code, Currency.exponent).where(Currency.code.in_(codes)))
    return {str(code): int(exponent) for code, exponent in rows}


def parse(db: Session, amount: str, code: str) -> tuple[int, CurrencyInfo]:
    """Amount text in a currency → minor units, or 422 with a stable code."""
    info = currency(db, code)
    try:
        return to_minor(amount, info.exponent), info
    except InvalidAmountError:
        raise ApiError(
            422,
            "invalid_amount",
            "Amount isn't a valid decimal for its currency",
            {"currency": info.code, "decimals": info.exponent},
        ) from None
