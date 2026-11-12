"""Characters that can make text read differently from what it says."""

import unicodedata

# Direction overrides and isolates. Joiners (U+200C, U+200D) stay: Arabic, Persian and emoji need
# them, and they can't reorder anything.
_REORDERING = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def unsafe(text: str, *, allow_newline: bool = False) -> bool:
    """Control characters, or anything that would reorder how text around it is displayed."""
    for char in text:
        if char == "\n" and allow_newline:
            continue
        if char in _REORDERING or unicodedata.category(char) == "Cc":
            return True
    return False
