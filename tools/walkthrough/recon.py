import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081/"

def describe(el):
    try:
        return {
            "tag": el.tag_name,
            "id": el.get_attribute("id"),
            "cls": (el.get_attribute("class") or "")[:60],
            "text": (el.inner_text() or "").strip()[:48],
            "data": {k: v for k, v in (el.evaluate(
                "e => Object.fromEntries([...e.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value]))"
            ) or {}).items()},
        }
    except Exception as e:
        return {"err": str(e)[:60]}

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 430, "height": 820})
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(1500)

    out = {"title": pg.title(), "url": pg.url}

    # What's visible first?
    out["visible_panels"] = pg.evaluate(
        "() => [...document.querySelectorAll('.view-panel')].map(e=>({id:e.id, hidden:e.classList.contains('hidden')}))"
    )
    out["tabs"] = pg.evaluate(
        "() => [...document.querySelectorAll('.tab-btn')].map(e=>({tab:e.dataset.tab, text:e.innerText.trim().slice(0,30), active:e.classList.contains('active')}))"
    )

    # Open agenda: prefer the tab button
    nav = pg.query_selector('.tab-btn[data-tab="agenda"]') or pg.query_selector('.dashboard-nav-card[data-tab="agenda"]')
    out["agenda_trigger_found"] = bool(nav)
    if nav:
        nav.click()
        pg.wait_for_timeout(2000)

    out["agenda_hidden_after"] = pg.evaluate(
        "() => document.getElementById('view-agenda')?.classList.contains('hidden')"
    )

    # Interactive elements inside agenda
    for sel_scope in ["#agenda-left", "#agenda-center"]:
        els = pg.query_selector_all(
            f"{sel_scope} button, {sel_scope} a, {sel_scope} input, {sel_scope} select, {sel_scope} [role=button], {sel_scope} [contenteditable=true], {sel_scope} [onclick]"
        )
        out[sel_scope] = [describe(e) for e in els][:40]

    # center html snippet
    center = pg.query_selector("#agenda-center")
    if center:
        out["agenda_center_html_head"] = center.inner_html()[:1500]

    out["console_errors"] = errs[:10]

    with open("/var/folders/tz/015v2xtj0wdbq7n9pq2_k8440000gn/T/opencode/rec/recon.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    b.close()

print("OK -> recon.json")
print(json.dumps(out, ensure_ascii=False)[:4000])
