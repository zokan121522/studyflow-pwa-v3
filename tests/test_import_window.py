"""Tests for calendar_import.window — the 59/61/60-day boundaries."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import.window import (  # noqa: E402
    DEFAULT_DAYS,
    JOB_DAYS,
    VALID_DAYS,
    InvalidWindow,
    import_window,
    in_window,
)

TODAY = date(2026, 9, 30)


def test_sixty_days_is_offered():
    """The user asked for "2 meses"; 60 is a real option, not a special case."""
    assert 60 in VALID_DAYS
    assert JOB_DAYS == 60


def test_window_is_exactly_the_requested_span():
    start, end = import_window(60, today=TODAY)
    assert start == TODAY
    assert end == TODAY + timedelta(days=60)


def test_fifty_nine_days_is_inside():
    start, end = import_window(60, today=TODAY)
    assert in_window(start + timedelta(days=59), start, end) is True


def test_sixty_first_day_is_outside():
    """Half-open: the boundary day belongs to the next window."""
    start, end = import_window(60, today=TODAY)
    assert in_window(start + timedelta(days=60), start, end) is False


def test_the_end_boundary_itself_is_excluded():
    start, end = import_window(30, today=TODAY)
    assert in_window(end, start, end) is False


def test_today_itself_is_inside():
    start, end = import_window(7, today=TODAY)
    assert in_window(start, start, end) is True


@pytest.mark.parametrize("bad", [0, -1, 1, 29, 31, 61, None, "30", 30.0, True])
def test_invalid_day_counts_are_rejected(bad):
    """Floats and bools included: 30.0 == 30, and True == 1.

    A non-int day count means the caller computed something wrong, so the
    type is rejected before the value is even looked at.
    """
    with pytest.raises(InvalidWindow):
        import_window(bad, today=TODAY)


def test_error_message_names_the_allowed_values():
    with pytest.raises(InvalidWindow) as exc:
        import_window(45, today=TODAY)
    assert "60" in str(exc.value)


@pytest.mark.parametrize("days", VALID_DAYS)
def test_every_offered_option_builds_a_window(days):
    start, end = import_window(days, today=TODAY)
    assert end - start == timedelta(days=days)


def test_default_is_thirty_days():
    start, end = import_window(today=TODAY)
    assert end - start == timedelta(days=DEFAULT_DAYS)