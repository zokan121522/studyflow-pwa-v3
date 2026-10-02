"""The import window: [today, today + days).

Pure, so the boundary rules are testable without a database or a calendar.
The window is half-open — an event exactly on `window_end` is *outside* —
and the whole point is that sessions which fall out are never deleted, just
stale. Keeping that in one small module makes it obvious.

59 days in, 61 days out: the user asked for "two months", which is what 60
gives them, and 60 is a selectable option rather than a silent default.
"""

from datetime import date, timedelta

# 60 = the "2 meses" option the user asked for.
VALID_DAYS = (7, 15, 30, 60)

DEFAULT_DAYS = 30

# The daily job uses the widest window so nothing gets dropped from the
# agenda just because the user only opened the panel with "7d" once.
JOB_DAYS = 60


class InvalidWindow(ValueError):
    """Raised for a day count that is not one of VALID_DAYS."""


def import_window(days=DEFAULT_DAYS, today=None):
    """Return ``(start, end)`` as dates; the range is [start, end).

    ``today`` is injectable so the boundaries are testable without freezing
    the clock. ``end`` is exclusive.
    """
    # Strict on type, not just value: 30.0 == 30 would otherwise slip
    # through the membership test, and a non-int day count means the caller
    # computed something wrong. bool is an int, hence the explicit guard.
    if isinstance(days, bool) or not isinstance(days, int):
        raise InvalidWindow(
            f"days must be an int, one of {VALID_DAYS}, got {days!r}"
        )
    if days not in VALID_DAYS:
        raise InvalidWindow(
            f"days must be one of {VALID_DAYS}, got {days!r}"
        )
    start = today or date.today()
    return start, start + timedelta(days=days)


def in_window(day, start, end):
    """True when ``day`` falls inside the half-open window."""
    return start <= day < end