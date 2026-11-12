"""Bounds on what clients may send. Outside them a request is refused, never a server error."""

from datetime import date

from app.core.errors import ApiError

# Far wider than any real ledger. Date arithmetic near year 1 or 9999 would overflow.
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2199, 12, 31)
MONTH_PATTERN = r"^(?:19|20|21)\d{2}-(0[1-9]|1[0-2])$"
# No request carries anything close to this. Exports are responses, not requests.
MAX_BODY_BYTES = 1_048_576


def check_date(value: date | None) -> None:
    if value is not None and not MIN_DATE <= value <= MAX_DATE:
        raise ApiError(422, "date_out_of_range", "That date is out of range")
