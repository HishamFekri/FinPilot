"""Splitting a trip expense among beneficiaries (FINANCIAL_RULES.md §8). Pure: no database.

1. Split the original total by the chosen method: shares sum exactly to the total.
2. Convert the total once (the caller does that, §8.2).
3. Allocate the converted total in proportion to the original shares: sums exactly too.

Largest remainder, with ties broken by sha256(expense_id:member_id), so the extra minor unit
doesn't always land on the same person, and the same expense always splits the same way.
Integers and Fractions only; never floats.
"""

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from app.core.errors import ApiError

METHODS = ("equal", "fixed", "percentage")
MAX_PERCENT_PLACES = 4


@dataclass(frozen=True)
class ShareInput:
    user_id: uuid.UUID
    value: str | None = None  # a fixed amount or a percentage, as text; None for "equal"


@dataclass(frozen=True)
class Share:
    user_id: uuid.UUID
    input_value: Decimal | None
    share_minor: int


def _tiebreak(expense_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return hashlib.sha256(f"{expense_id}:{user_id}".encode()).hexdigest()


def allocate(
    total: int, weights: dict[uuid.UUID, Fraction | int], expense_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """`total` minor units in proportion to `weights` (non-negative). Sums exactly to `total`;
    a zero weight gets zero; deterministic for the same expense."""
    if total < 0:
        raise ValueError("total must not be negative")
    if any(Fraction(w) < 0 for w in weights.values()):
        raise ValueError("weights must not be negative")
    whole = sum((Fraction(w) for w in weights.values()), Fraction(0))
    if whole == 0:
        raise ValueError("at least one weight must be positive")
    shares: dict[uuid.UUID, int] = {}
    remainders: dict[uuid.UUID, Fraction] = {}
    for member, weight in weights.items():
        exact = Fraction(total) * Fraction(weight) / whole
        shares[member] = exact.numerator // exact.denominator
        remainders[member] = exact - shares[member]
    leftover = total - sum(shares.values())
    order = sorted(
        (m for m, w in weights.items() if Fraction(w) > 0),
        key=lambda m: (-remainders[m], _tiebreak(expense_id, m)),
    )
    for member in order[:leftover]:
        shares[member] += 1
    return shares


def _decimal(text: str, field: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        raise ApiError(422, "validation_error", f"The {field} isn't a number") from None
    if not value.is_finite():
        raise ApiError(422, "validation_error", f"The {field} isn't a number")
    return value


def split(
    total: int, exponent: int, method: str, inputs: list[ShareInput], expense_id: uuid.UUID
) -> list[Share]:
    """Original-currency shares. Every beneficiary ends with a positive share, or 422."""
    if method not in METHODS:
        raise ApiError(422, "validation_error", "Unknown split method")
    if not inputs:
        raise ApiError(422, "beneficiaries_required", "Choose at least one person")
    ids = [i.user_id for i in inputs]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "validation_error", "Each person can appear once")

    values: dict[uuid.UUID, Decimal | None] = {}
    if method == "equal":
        weights: dict[uuid.UUID, Fraction | int] = dict.fromkeys(ids, 1)
        values = dict.fromkeys(ids)
        minor = allocate(total, weights, expense_id)
    elif method == "fixed":
        unit = Decimal(1).scaleb(-exponent)
        minor = {}
        for item in inputs:
            amount = _decimal(item.value or "", "amount")
            if amount != amount.quantize(unit) or amount <= 0:
                raise ApiError(
                    422,
                    "invalid_amount",
                    "Each amount must be positive, with at most the currency's decimals",
                    {"decimals": exponent},
                )
            values[item.user_id] = amount
            minor[item.user_id] = int(amount.scaleb(exponent))
        if sum(minor.values()) != total:
            raise ApiError(422, "split_sum_mismatch", "The amounts must add up to the total")
    else:
        percents: dict[uuid.UUID, Fraction | int] = {}
        for item in inputs:
            percent = _decimal(item.value or "", "percentage")
            if percent <= 0 or percent != percent.quantize(Decimal(1).scaleb(-MAX_PERCENT_PLACES)):
                raise ApiError(
                    422,
                    "validation_error",
                    "Each percentage must be positive, with at most 4 decimals",
                )
            values[item.user_id] = percent
            percents[item.user_id] = Fraction(percent)
        if sum(percents.values()) != 100:
            raise ApiError(422, "percentage_sum_invalid", "The percentages must add up to 100")
        minor = allocate(total, percents, expense_id)

    if any(v <= 0 for v in minor.values()):
        raise ApiError(
            422,
            "share_rounds_to_zero",
            "Someone's share would be zero; split among fewer people or use amounts",
        )
    return [Share(uid, values.get(uid), minor[uid]) for uid in ids]


def base_shares(
    base_total: int, shares: list[Share], expense_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """The converted total in proportion to the original shares (§8.1 step 3)."""
    return allocate(base_total, {s.user_id: s.share_minor for s in shares}, expense_id)


def suggest(nets: dict[uuid.UUID, int]) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Suggested payments (debtor, creditor, amount) that bring every net to zero (§10).

    Deterministic, at most k-1 payments; not necessarily the fewest (the UI says "suggested").
    """
    if sum(nets.values()) != 0:
        raise ValueError("nets must sum to zero")
    creditors = {m: n for m, n in nets.items() if n > 0}
    debtors = {m: -n for m, n in nets.items() if n < 0}
    payments: list[tuple[uuid.UUID, uuid.UUID, int]] = []
    while creditors and debtors:
        c = min(creditors, key=lambda m: (-creditors[m], str(m)))
        d = min(debtors, key=lambda m: (-debtors[m], str(m)))
        amount = min(creditors[c], debtors[d])
        payments.append((d, c, amount))
        creditors[c] -= amount
        debtors[d] -= amount
        if creditors[c] == 0:
            del creditors[c]
        if debtors[d] == 0:
            del debtors[d]
    return payments
