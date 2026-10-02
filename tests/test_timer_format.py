"""formatTimer must never leak a fractional second into the UI.

Postgres `EXTRACT(EPOCH FROM <timestamp diff>)` returns fractional seconds, so
the session timer reached the frontend as values like `9.354299999999995`.
`formatTimer` did `const s = seconds % 60`, which kept the fraction and printed

    ⏱ 00:02:9.354299999999995

The value is now floored. These pin that, including the exact input the bug
produced — a naive "does it contain a dot" check would miss 129.3543 style
inputs whose fraction happened to truncate.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

UI_COMMON = Path(__file__).resolve().parents[1] / "frontend" / "shared" / "ui-common.js"
SOURCE = UI_COMMON.read_text(encoding="utf-8")

# The whole function, verbatim, so the tests exercise the shipped code rather
# than a transcription of it.
FN_SRC = re.search(
    r"function formatTimer\(seconds\)\s*\{.*?\n\}", SOURCE, re.DOTALL
)
assert FN_SRC, "formatTimer not found in ui-common.js"
FN_TEXT = FN_SRC.group(0)


def _js_format(argument: str) -> str:
    """Call the real formatTimer in node with `argument` as its parameter."""
    # Wrapped in parens: eval() of a bare declaration returns undefined.
    expr = json.dumps("(" + FN_TEXT + ")")
    script = f"const f = eval({expr}); console.log(JSON.stringify(f({argument})));"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


class TestFormatTimerFloors:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "00:00:00"),
            (1, "00:00:01"),
            (59, "00:00:59"),
            (60, "00:01:00"),
            (3599, "00:59:59"),
            (3600, "01:00:00"),
            (7325, "02:02:05"),
        ],
    )
    def test_whole_seconds(self, seconds, expected):
        assert _js_format(str(seconds)) == expected

    @pytest.mark.parametrize(
        "seconds",
        [
            9.354299999999995,      # the value the bug actually produced
            129.354299999999995,    # same, inside a minute
            0.999999,
            59.999999,              # must not round up to 60 and roll the minute
            3599.5,
            86399.999999,
        ],
    )
    def test_fractional_input_never_shows_a_decimal(self, seconds):
        out = _js_format(repr(seconds))
        assert "." not in out, f"{seconds} rendered as {out}"
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", out), out

    def test_59_999_rolls_down_not_up(self):
        # The subtle one: naive rounding would give 60 and turn 00:00:59 into
        # 00:01:00, showing a minute that has not passed.
        assert _js_format("59.999999") == "00:00:59"

    def test_negative_and_undefined(self):
        assert _js_format("-1") == "00:00:00"
        assert _js_format("undefined") == "00:00:00"
        assert _js_format("null") == "00:00:00"


class TestBackendTruncatesAtTheSource:
    """Belt and braces: the column should not store fractions either."""

    STATE = (
        Path(__file__).resolve().parents[1] / "backend" / "routes" / "agenda_state.py"
    ).read_text(encoding="utf-8")

    def test_epoch_deltas_are_floored(self):
        for name in ("_PAUSED_DELTA", "_WALL_DELTA"):
            block = re.search(
                name + r"\s*=\s*\((.*?)\n\s*\)", self.STATE, re.DOTALL
            )
            assert block, f"{name} not found"
            assert "FLOOR(" in block.group(1), (
                f"{name} must FLOOR: EXTRACT(EPOCH ...) returns fractional "
                "seconds and they reached the UI"
            )

    def test_no_bare_unfloored_epoch_delta_remains(self):
        # A COALESCE(EXTRACT(EPOCH ...)) without FLOOR is the bug returning.
        assert not re.search(r"COALESCE\(\s*\n?\s*EXTRACT\(EPOCH", self.STATE), (
            "an unfloored EXTRACT(EPOCH ...) delta is still present"
        )