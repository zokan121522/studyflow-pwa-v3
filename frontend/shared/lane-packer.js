// ── lane-packer.js ─────────────────────────────────────────────────────
// Assigns horizontal lanes to timed sessions so that overlapping events
// sit side by side instead of stacking on top of each other.
//
// Why this file exists: the week and day timelines used to position every
// block at `left:1px; right:1px` — full column width, unconditionally. Two
// sessions at the same hour therefore occupied the exact same rectangle and
// only the last one in DOM order was visible. The data confirms it was not
// a rare edge case: the database holds 50 overlapping pairs, 16 of them with
// byte-identical intervals.
//
// This is the same shape of problem calendar apps solve with a sweep line
// over clusters. It is deliberately kept in its own file with no DOM access
// so it can be unit tested against the pathological cases (identical
// intervals, containment, chain overlap, overnight).
//
// Usage:
//
//     var lanes = App.Lanes.pack(sessions, {
//       start: function (s) { return s.start_time; },
//       end:   function (s) { return s.end_time;   }
//     });
//     // sessions are decorated with ._lane = { index, total }
//
// ── Semantics worth stating, because they are choices ──────────────────
//
//   * Touching is not overlapping. A class ending at 10:00 and another
//     starting at 10:00 are not simultaneous, and must not be split into
//     two half-width columns. Intervals are half-open: [start, end).
//
//   * `total` is per-cluster, not per-day. A single overlapping pair splits
//     only itself into two columns; it does not narrow every other event
//     in the day. Clusters are the maximal groups connected by overlap.
//
//   * Containment shares lanes. A 3-hour block overlapped by two 1-hour
//     blocks gets one lane; the two short ones split the other. The wide
//     block keeps full height and stays readable rather than being chopped
//     into the same narrow column as its neighbours.
//
(function (global) {
  "use strict";

  // ── minutesFromHHMM("17:30") → 1050 ───────────────────────────────
  // Returns null for anything unparseable, which the caller treats as
  // "unscheduled" and routes out of the calendar entirely.
  function minutesFromHHMM(value) {
    if (typeof value !== "string") return null;
    var m = value.trim().match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return null;
    var h = parseInt(m[1], 10);
    var min = parseInt(m[2], 10);
    if (isNaN(h) || isNaN(min) || h < 0 || h > 24 || min < 0 || min > 59) return null;
    return h * 60 + min;
  }

  // ── pack(items, opts) → mutates items with ._lane ─────────────────
  // opts.getStart / opts.getEnd return "HH:MM" strings.
  // opts.fallbackMinutes is the duration assumed when an end is missing,
  // and opts.clipAt is the number of minutes past midnight that an
  // unterminated interval is assumed to run to.
  function pack(items, opts) {
    opts = opts || {};
    var getStart = opts.getStart || function (s) { return s.start_time; };
    var getEnd = opts.getEnd || function (s) { return s.end_time; };
    var fallbackMinutes = opts.fallbackMinutes || 60;
    var clipAt = typeof opts.clipAt === "number" ? opts.clipAt : 24 * 60;

    var entries = [];
    for (var i = 0; i < items.length; i++) {
      var s = items[i];
      var start = minutesFromHHMM(getStart(s));
      if (start === null) continue; // unscheduled — handled elsewhere
      var end = minutesFromHHMM(getEnd(s));
      if (end === null || end <= start) end = Math.min(start + fallbackMinutes, clipAt);
      entries.push({ item: s, start: start, end: end, order: i });
    }

    // Sorted by start, then by duration descending. The duration tiebreak
    // matters: without it, a short event that happens to be listed first
    // would claim lane 0 and push the long block that contains it into a
    // narrow neighbour lane. Contained events sharing a lane is the
    // behaviour we want, so the longer block goes first and holds the lane.
    entries.sort(function (a, b) {
      if (a.start !== b.start) return a.start - b.start;
      if (a.end !== b.end) return b.end - a.end;
      return a.order - b.order;
    });

    // Sweep: collect maximal clusters of transitively overlapping entries,
    // then lay each cluster out independently.
    var cluster = [];
    var clusterEnd = -1;

    function flush() {
      if (!cluster.length) return;
      assignCluster(cluster);
      cluster = [];
      clusterEnd = -1;
    }

    for (var j = 0; j < entries.length; j++) {
      var e = entries[j];
      // Half-open: an entry starting exactly at clusterEnd does not join
      // the cluster. This is what keeps back-to-back classes full width.
      if (cluster.length && e.start >= clusterEnd) flush();
      cluster.push(e);
      if (e.end > clusterEnd) clusterEnd = e.end;
    }
    flush();

    return items;
  }

  // ── assignCluster: greedy first-fit into lanes ────────────────────
  function assignCluster(cluster) {
    var laneEnds = []; // laneEnds[i] = when lane i becomes free

    for (var i = 0; i < cluster.length; i++) {
      var e = cluster[i];
      var lane = -1;

      // Prefer the lane whose last occupant ended latest before this
      // event. That reuses a lane that is genuinely free rather than
      // opening a fresh one when a free one already exists, which keeps
      // the column count as low as the data allows.
      var bestEnd = -1;
      for (var j = 0; j < laneEnds.length; j++) {
        if (laneEnds[j] <= e.start && laneEnds[j] > bestEnd) {
          bestEnd = laneEnds[j];
          lane = j;
        }
      }
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(e.end);
      } else {
        laneEnds[lane] = e.end;
      }

      e.item._lane = { index: lane, total: 1 };
    }

    // A lane left empty because its event was the only one in the cluster
    // should not narrow anything, so the divisor is the number of lanes
    // actually used, which is laneEnds.length after the sweep.
    var total = laneEnds.length || 1;
    for (var k = 0; k < cluster.length; k++) {
      cluster[k].item._lane.total = total;
    }
  }

  global.App = global.App || {};
  global.App.Lanes = {
    pack: pack,
    minutesFromHHMM: minutesFromHHMM
  };

  // Exported for the Node test harness, which has no window.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = global.App.Lanes;
  }
})(typeof window !== "undefined" ? window : globalThis);