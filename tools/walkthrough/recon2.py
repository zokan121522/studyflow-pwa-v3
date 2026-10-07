import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081/"

JS_DESC = """
e => ({
  tag: e.tagName.toLowerCase(),
  id: e.id || null,
  cls: (e.className || '').toString().slice(0, 70),
  type: e.getAttribute('type'),
  name: e.getAttribute('name'),
  ph: e.getAttribute('placeholder'),
  text: (e.innerText || e.value || '').trim().slice(0, 40),
  action: e.getAttribute('data-action'),
  view: e.getAttribute('data-view'),
  aria: e.getAttribute('aria-label'),
})
"""

def scan(pg, scope):
    els = pg.query_selector_all(
        f"{scope} button, {scope} a, {scope} input, {scope} select, {scope} textarea, "
        f"{scope} [role=button], {scope} [contenteditable=true], {scope} [data-action]"
    )
    return [e.evaluate(JS_DESC) for e in els][:60]

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 430, "height": 820})
    pg = ctx.new_page()
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(1200)
    pg.query_selector('.tab-btn[data-tab="agenda"]').click()
    pg.wait_for_timeout(1800)

    out = {}
    out["agenda_left"] = scan(pg, "#agenda-left")
    out["agenda_center"] = scan(pg, "#agenda-center")

    # Open the "add session" flow
    btn = pg.query_selector('.ht-btn[data-action="add-session"]')
    out["add_session_found"] = bool(btn)
    if btn:
        btn.click()
        pg.wait_for_timeout(1200)
        out["overlays_after_add"] = pg.evaluate("""
        () => [...document.querySelectorAll('[class*=modal],[role=dialog],[class*=overlay],[class*=popover]')]
              .map(e=>({cls:(e.className||'').toString().slice(0,60), id:e.id,
                        hidden:e.classList.contains('hidden')||getComputedStyle(e).display==='none',
                        h:e.offsetHeight}))
        """)
        # dump the visible dialog
        dlg = pg.query_selector("[role=dialog]") or pg.query_selector("[class*=modal]:not(.hidden)")
        if dlg:
            out["dialog_html_head"] = dlg.inner_html()[:2500]
            out["dialog_fields"] = scan(pg, "[role=dialog], [class*=modal]")

    with open("/var/folders/tz/015v2xtj0wdbq7n9pq2_k8440000gn/T/opencode/rec/recon2.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    b.close()

print("OK -> recon2.json")
print("ADD_SESSION:", out["add_session_found"])
print("\n--- agenda_center (compacto) ---")
for e in out.get("agenda_center", []):
    print(f"  {e['tag']:6} act={e['action']} view={e['view']} type={e['type']} txt={e['text']!r} cls={e['cls'][:35]}")
print("\n--- overlays tras add ---")
for o in out.get("overlays_after_add", []):
    print(f"  hidden={o['hidden']} h={o['h']} id={o['id']} cls={o['cls'][:40]}")
