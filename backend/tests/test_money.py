"""Money parsing and formatting (FINANCIAL_RULES.md §2). No database, no floats."""

import pytest

from app.services.money import InvalidAmountError, to_amount, to_minor


@pytest.mark.parametrize(
    ("text", "exponent", "minor"),
    [
        ("0", 2, 0),
        ("0.5", 2, 50),
        ("12.50", 2, 1250),
        ("1250.5", 2, 125050),
        ("-12.34", 2, -1234),
        ("1500", 0, 1500),  # JPY
        ("1.250", 3, 1250),  # KWD
        ("0.001", 3, 1),
        ("10000000000000.00", 2, 10**15),  # the cap, exactly (M6)
        ("0.1", 4, 1000),
    ],
)
def test_canonical_amounts_become_exact_minor_units(text: str, exponent: int, minor: int) -> None:
    assert to_minor(text, exponent) == minor


@pytest.mark.parametrize(
    ("text", "exponent"),
    [
        ("12.345", 2),  # more decimals than the currency has: refused, never rounded (M3)
        ("1.5", 0),
        ("12.", 2),
        (".5", 2),
        ("+12", 2),
        ("012", 2),
        ("1,234.50", 2),  # thousands separators are the web app's business (M7)
        ("1.234,50", 2),
        ("12,50", 2),
        ("1e3", 2),
        ("NaN", 2),
        ("Infinity", 2),
        (" 12", 2),
        ("12 ", 2),
        ("", 2),
        ("-", 2),
        ("-0", 2),
        ("-0.00", 2),
        ("١٢", 2),  # Arabic-Indic digits are normalised by the web app, not here
        ("10000000000000.01", 2),  # one minor unit over the cap
        ("99999999999999999", 0),
    ],
)
def test_anything_else_is_refused(text: str, exponent: int) -> None:
    with pytest.raises(InvalidAmountError):
        to_minor(text, exponent)


@pytest.mark.parametrize(
    ("minor", "exponent", "text"),
    [
        (0, 2, "0.00"),
        (5, 2, "0.05"),
        (125050, 2, "1250.50"),
        (-1234, 2, "-12.34"),
        (-5, 2, "-0.05"),
        (1500, 0, "1500"),
        (1, 3, "0.001"),
        (10**15, 2, "10000000000000.00"),
    ],
)
def test_minor_units_format_with_every_decimal(minor: int, exponent: int, text: str) -> None:
    assert to_amount(minor, exponent) == text


@pytest.mark.parametrize("exponent", [0, 2, 3, 4])
@pytest.mark.parametrize("minor", [0, 1, -1, 7, 99, 100, 101, 123456789, -987654321, 10**15])
def test_formatting_and_parsing_round_trip(minor: int, exponent: int) -> None:
    assert to_minor(to_amount(minor, exponent), exponent) == minor
