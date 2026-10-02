"""The status module must not be able to freeze the tab.

It was frozen once, and it is worth being precise about why. The fix for the
"chip stuck on comprobando…" problem was a MutationObserver that re-applies
the cached status whenever the chip is reinserted. But _apply() writes to
the chip's textContent, and that write is itself a DOM mutation under the
observed subtree — so the observer fired again, wrote again, forever. The tab
locked up on a busy agenda.

A content assertion cannot catch that. This executes the module against a
real MutationObserver in Node, drives it the way the app does, and fails if
the write count runs away. jsdom is not a dependency of this repo, so the DOM
surface it needs is stubbed to the four calls the module makes.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATUS_JS = ROOT / "frontend" / "features" / "agenda" / "calendar-import-status.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the module"
)

# Minimal stand-in for the parts of the DOM the module touches. Enough to run
# it for real, nowhere near enough to be a browser.
HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

let writes = 0;
const pending = [];

// A text node is what setting textContent adds. It matters that the harness
// reports it as nodeType 3: the fixed module ignores non-element nodes when
// deciding whether the chip itself was (re)inserted.
let autoFires = 0;
const AUTO_FIRE_CAP = 60;

// Lives in the sandbox so both the harness and the code evaluated inside the
// vm context can reach it — a `let` in this file is not visible from there.
const obs = { cb: null, active: false };

const chip = {
  nodeType: 1,
  classList: { contains: (c) => c === 'cal-import-status' },
  querySelector: () => null,
  _text: '',
  set textContent(v) {
    writes++;
    this._text = v;
    // This is the feedback loop. In a browser, writing textContent mutates
    // the observed subtree, which the observer then reports back — so the
    // module gets invoked again by its own write. Capped so a regression
    // shows up as a write count instead of hanging the test run.
    if (obs.active && autoFires < AUTO_FIRE_CAP) {
      autoFires++;
      obs.cb([{ addedNodes: [{ nodeType: 3 }] }]);
    }
  },
  get textContent() { return this._text; },
  style: {},
  title: ''
};

const document = {
  body: { nodeType: 1 },
  querySelector: (sel) => (sel === '.cal-import-status' ? chip : null)
};

const sandbox = {
  document,
  window: { addEventListener: () => {}, MutationObserver: null, console },
  console,
  obs,
  setTimeout, clearTimeout
};
sandbox.window.document = document;
vm.createContext(sandbox);

// The real MutationObserver: observe() records the callback, and a helper
// drives it. We need the genuine semantics to reproduce the feedback loop.
vm.runInContext(`
  class FakeObserver {
    constructor(cb) { this.cb = cb; }
    observe(target, opts) { window.__observer = this; obs.active = true; obs.cb = this.cb; }
    disconnect() { obs.active = false; }
  }
  window.MutationObserver = FakeObserver;
  MutationObserver = FakeObserver;
`, sandbox);

vm.runInContext(fs.readFileSync(%s, 'utf8'), sandbox);

const mod = sandbox.window.CalendarImportStatus;

// Turn a fetch result into a status and let the module cache it.
(async () => {
  // The module checks window.API before calling it — see refresh(). Setting a
  // bare global here would make it take the "API not loaded yet" path and
  // return without ever painting, which is a different test entirely.
  sandbox.window.API = { get: async () => ({ state: 'ok', message: 'Todo correcto' }) };
  sandbox.API = sandbox.window.API;
  await mod.refresh();
  const afterFetch = writes;

  // Now do what renderAgenda does: insert the chip.
  chip._text = '⏳ comprobando…';
  obs.cb([{ addedNodes: [chip] }]);
  const afterInsert = writes;

  // And again, as a second render would.
  obs.cb([{ addedNodes: [chip] }]);
  obs.cb([{ addedNodes: [chip] }]);
  const afterRenders = writes;

  // Delimited so the parser can pick it out from the module's own console
  // output, which is part of what we are exercising.
  console.log('__RESULT__' + JSON.stringify({
    autoFires,
    writesAfterFetch: afterFetch,
    writesTotal: writes,
    chipText: chip._text
  }));
})();
"""

STATUS_PATH_JSON = '"' + str(STATUS_JS).replace("\\", "/") + '"'


def _run():
    script = HARNESS % STATUS_PATH_JSON
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    import json
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    raise AssertionError(f"no result line in output:\n{proc.stdout}\n{proc.stderr}")


def test_a_normal_load_does_not_loop():
    """One fetch plus a reinsert is a handful of writes, not thousands."""
    r = _run()
    assert r["writesTotal"] < 20, (
        f"{r['writesTotal']} writes for one fetch and three reinserts — the "
        f"observer is feeding itself and the tab would lock up"
    )


def test_the_chip_ends_up_painted_not_on_the_placeholder():
    r = _run()
    assert "⏳" not in r["chipText"], (
        f"chip still shows the placeholder after a reinsert: {r['chipText']}"
    )
    assert "Todo correcto" in r["chipText"]


def test_the_module_has_an_explicit_reentrancy_guard():
    """The harness above catches a regression; this names the cause."""
    src = STATUS_JS.read_text()
    assert re.search(r"var\s+_applying\s*=\s*false", src), (
        "no re-entrancy guard: writing the chip's own textContent re-triggers "
        "the observer and freezes the page"
    )
    assert "finally" in src, "the guard must be released even if paint throws"


def test_the_observer_only_reacts_to_the_chip_being_added():
    """Watching every mutation in an app that re-renders constantly is a
    performance problem even when it does not loop."""
    src = STATUS_JS.read_text()
    assert "_observerWantsChip" in src
    assert "addedNodes" in src, (
        "must inspect addedNodes; acting on every mutation is how the "
        "feedback loop starts"
    )