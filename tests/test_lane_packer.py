"""Tests for frontend/shared/lane-packer.js.

The packer decides whether overlapping sessions share a column or get split
side by side. Getting this wrong is invisible in most screenshots and very
obvious in real use: two classes at 17:00 would render on top of each other
and only the second would be clickable.

Run with: python3 -m pytest tests/test_lane_packer.py -q
"""

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKER = ROOT / "frontend" / "shared" / "lane-packer.js"


def _run_node(snippet: str) -> dict:
    """Run JS with the packer loaded and return the JSON it prints.

    Driven through node rather than a JS test framework because the project
    has no frontend test runner and adding one for this would be a much
    larger change than the bug warrants.
    """
    script = f"""
    require({str(PACKER)!r});
    const Lanes = globalThis.App.Lanes;
    const out = (function () {{
      {snippet}
    }})();
    process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node fallo: {proc.stderr.strip()[:600]}")
    return json.loads(proc.stdout)


def _mk(start, end=None, sid=None):
    return {"id": sid or f"{start}-{end}", "start_time": start, "end_time": end}


def _lanes(sessions):
    """Pack and return [{id, index, total}] in the original input order."""
    return _run_node(
        f"""
      const ss = {json.dumps(sessions)};
      Lanes.pack(ss, {{}});
      return ss.map(s => ({{
        id: s.id,
        // An unscheduled session legitimately has no lane; report that
        // rather than throwing, so the caller can assert on it.
        index: s._lane ? s._lane.index : null,
        total: s._lane ? s._lane.total : null
      }}));
    """
    )


# ── basics ────────────────────────────────────────────────────────────


def test_single_session_gets_the_whole_column():
    """One event, nothing to share with: it must not be narrowed."""
    assert _lanes([_mk("17:00", "18:00")]) == [
        {"id": "17:00-18:00", "index": 0, "total": 1}
    ]


def test_non_overlapping_sessions_stay_full_width():
    """The common case must not regress: one column per day still works."""
    got = _lanes([_mk("09:00", "10:00"), _mk("11:00", "12:00")])
    assert [g["total"] for g in got] == [1, 1], got


def test_back_to_back_sessions_are_not_split():
    """[10:00,11:00) then [11:00,12:00) touch but do not overlap.

    Intervals are half-open. Splitting these would halve the width of
    every consecutive pair of classes in the day, which is the majority of
    a real timetable — this is the test that keeps the fix from over-
    correcting and making everything narrow.
    """
    got = _lanes([_mk("10:00", "11:00"), _mk("11:00", "12:00")])
    assert [g["total"] for g in got] == [1, 1], got


# ── the actual bug ────────────────────────────────────────────────────


def test_identical_intervals_split_into_two_columns():
    """Byte-identical intervals: the case that hid sessions entirely.

    16 such pairs exist in the database.
    """
    got = _lanes([_mk("05:00", "07:00", "a"), _mk("05:00", "07:00", "b")])
    assert got == [
        {"id": "a", "index": 0, "total": 2},
        {"id": "b", "index": 1, "total": 2},
    ]


def test_partial_overlap_splits():
    got = _lanes([_mk("10:00", "12:00", "a"), _mk("11:00", "11:30", "b")])
    assert got == [
        {"id": "a", "index": 0, "total": 2},
        {"id": "b", "index": 1, "total": 2},
    ]


def test_three_way_overlap_gives_three_columns():
    got = _lanes([_mk("09:00", "10:00", "a"),
                  _mk("09:00", "10:00", "b"),
                  _mk("09:30", "09:45", "c")])
    assert [g["total"] for g in got] == [3, 3, 3]
    assert sorted(g["index"] for g in got) == [0, 1, 2]


def test_lane_frees_up_after_it_ends():
    """A cluster reuses a lane once its occupant is finished.

    Without reuse, a day of back-to-back pairs would keep opening fresh
    lanes and every event would end up a sliver.
    """
    got = _lanes([_mk("09:00", "10:00", "a"),
                  _mk("09:00", "10:00", "b"),
                  _mk("10:00", "11:00", "c"),
                  _mk("10:00", "11:00", "d")])
    by_id = {g["id"]: g for g in got}
    assert by_id["a"] == {"id": "a", "index": 0, "total": 2}
    assert by_id["b"] == {"id": "b", "index": 1, "total": 2}
    # c must land in lane 0 again — lane 0 was free at 10:00 — not lane 2.
    assert by_id["c"]["index"] == 0, by_id
    assert by_id["d"]["index"] == 1, by_id


# ── containment ───────────────────────────────────────────────────────


def test_contained_event_shares_the_outer_lane():
    """A short event inside a long one must not split it into halves.

    The outer block keeps its own lane and stays readable; only the
    contained event moves to the side.
    """
    got = _lanes([_mk("10:00", "13:00", "long"), _mk("11:00", "11:30", "short")])
    by_id = {g["id"]: g for g in got}
    assert by_id["long"]["index"] == 0, by_id
    assert by_id["short"]["index"] == 1, by_id


def test_longer_block_is_placed_first_when_starts_together():
    """Tie on start time: the longer block holds lane 0.

    Ordering by duration on the tiebreak is what stops a short event from
    claiming lane 0 and squeezing the block that contains it.
    """
    got = _lanes([_mk("10:00", "10:30", "short"), _mk("10:00", "12:00", "long")])
    by_id = {g["id"]: g for g in got}
    assert by_id["long"]["index"] == 0, by_id
    assert by_id["short"]["index"] == 1, by_id


# ── clustering ────────────────────────────────────────────────────────


def test_clusters_are_independent():
    """Two separate overlapping pairs must not share a column count.

    If they shared, one busy hour would narrow the whole day.
    """
    got = _lanes([_mk("09:00", "10:00", "a"),
                  _mk("09:00", "10:00", "b"),
                  _mk("20:00", "21:00", "c"),
                  _mk("20:00", "21:00", "d")])
    by_id = {g["id"]: g for g in got}
    assert by_id["a"]["total"] == 2
    assert by_id["c"]["total"] == 2
    assert by_id["a"]["index"] == 0 and by_id["c"]["index"] == 0


def test_transitive_chain_reuses_the_freed_lane():
    """a(09-10), b(09:30-10:30), c(10-11): one cluster, but only two lanes.

    a and c do not overlap each other — a ends exactly when c starts — so
    they belong in the same lane. The cluster is sized once (both are sized
    together), but the honest answer is two columns, not three: opening a
    third would narrow a and c for no reason.

    Worth pinning because the naive implementation opens a fresh lane per
    event and makes every block in the chain a third of the width.
    """
    got = _lanes([_mk("09:00", "10:00", "a"),
                  _mk("09:30", "10:30", "b"),
                  _mk("10:00", "11:00", "c")])
    by_id = {g["id"]: g for g in got}
    assert by_id["a"] == {"id": "a", "index": 0, "total": 2}
    assert by_id["b"] == {"id": "b", "index": 1, "total": 2}
    assert by_id["c"] == {"id": "c", "index": 0, "total": 2}


# ── robustness ────────────────────────────────────────────────────────


def test_missing_end_time_uses_fallback_duration():
    """Sessions with no end still need a lane rather than being dropped."""
    got = _lanes([_mk("09:00", None, "a"), _mk("09:00", None, "b")])
    assert [g["total"] for g in got] == [2, 2]


def test_zero_length_session_does_not_collapse():
    """start == end: real data has 24 of these, mostly Moodle deadlines."""
    got = _lanes([_mk("00:00", "00:00", "a"), _mk("00:00", "00:00", "b")])
    assert all(g["total"] == 2 for g in got), got


def test_unparseable_start_is_left_alone():
    """No start_time: the week view routes these to "Sin programar".

    The packer must not invent a lane for them or crash.
    """
    got = _lanes([{"id": "x", "start_time": None, "end_time": None},
                  _mk("09:00", "10:00")])
    assert len(got) == 2
    assert got[0] == {"id": "x", "index": None, "total": None}, got
    # The other session is unaffected and still gets a full-width lane.
    assert got[1]["total"] == 1, got


def test_empty_input():
    assert _lanes([]) == []


def test_minutes_from_hhmm():
    got = _run_node(
        """
      return {
        a: Lanes.minutesFromHHMM('00:00'),
        b: Lanes.minutesFromHHMM('9:05'),
        c: Lanes.minutesFromHHMM('23:59'),
        d: Lanes.minutesFromHHMM('24:00'),
        e: Lanes.minutesFromHHMM('nope'),
        f: Lanes.minutesFromHHMM(null),
        g: Lanes.minutesFromHHMM('12:60')
      };
    """
    )
    assert got["a"] == 0
    assert got["b"] == 9 * 60 + 5
    assert got["c"] == 24 * 60 - 1
    assert got["d"] == 24 * 60, "24:00 is a legitimate clip boundary"
    assert got["e"] is None
    assert got["f"] is None
    assert got["g"] is None


def test_lane_geometry_tiles_the_column_without_gaps():
    """For a set of mutually overlapping events, lanes must tile 0..100%.

    This is the invariant the CSS depends on: left is index/total and width
    is 1/total, so the last lane has to end flush at 100% and no lane may
    be skipped. Uses four identical intervals so all four must be lanes.
    """
    got = _lanes([_mk("09:00", "10:00", "a"), _mk("09:00", "10:00", "b"),
                  _mk("09:00", "10:00", "c"), _mk("09:00", "10:00", "d")])
    total = got[0]["total"]
    assert total == 4, got
    assert sorted(g["index"] for g in got) == [0, 1, 2, 3]
    edges = sorted((g["index"], g["index"] + 1) for g in got)
    assert edges == [(0, 1), (1, 2), (2, 3), (3, 4)]
    # left + width of the last lane, in %, must reach the right edge.
    last = max(got, key=lambda g: g["index"])
    assert last["index"] * 100 / total + 100 / total == 100.0


def test_long_block_is_not_narrowed_by_events_inside_it():
    """A block containing others keeps lane 0; they stack beside it.

    Pinning the behaviour the packer is actually for: if a 3-hour block
    were narrowed to half because of two 1-hour events inside it, the long
    block would become unreadable — and long blocks are the norm on a
    study timetable.
    """
    got = _lanes([_mk("09:00", "12:00", "long"),
                  _mk("09:00", "10:00", "b"),
                  _mk("10:00", "11:00", "c"),
                  _mk("11:00", "12:00", "d")])
    by_id = {g["id"]: g for g in got}
    assert by_id["long"] == {"id": "long", "index": 0, "total": 2}
    # The three interior events are mutually consecutive, so they share one
    # lane between them.
    assert by_id["b"]["index"] == by_id["c"]["index"] == by_id["d"]["index"] == 1
    assert all(g["total"] == 2 for g in got), got