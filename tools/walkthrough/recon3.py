import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081/"
JS_DESC = """
e => ({
  tag: e.tagName.toLowerCase(), id: e.id||null, cls:(e.className||'').toString().slice(0,50),
  type:e.getAttribute('type'), name:e.getAttribute('name'), ph:e.getAttribute('placeholder'),
  text:(e.innerText||e.value||'').trim().slice(0,45), action:e.getAttribute('data-action')
})
"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_context(viewport={"width":430,"height":820}).new_page()
    pg.goto(BASE, wait_until="networkidle"); pg.wait_for_timeout(1200)
    pg.query_selector('.tab-btn[data-tab="agenda"]').click(); pg.wait_for_timeout(1500)
    pg.query_selector('.ht-btn[data-action="add-session"]').click(); pg.wait_for_timeout(1000)

    ov = pg.query_selector("#session-overlay")
    out = {}
    out["visible"] = bool(ov and ov.bounding_box())
    out["head_text"] = ov.query_selector(".omodal-head").inner_text().strip() if ov else None
    out["fields"] = [e.evaluate(JS_DESC) for e in ov.query_selector_all("button,input,select,textarea,[data-action]")] if ov else []
    out["html"] = ov.inner_html()[:3000] if ov else None
    print("visible:", out["visible"])
    print("head:", out["head_text"])
    print("--- fields ---")
    for f in out["fields"]:
        print(f"  {f['tag']:7} id={f['id']} cls={f['cls'][:24]:24} type={f['type']} ph={f['ph']} txt={f['text']!r}")
    with open("/var/folders/tz/015v2xtj0wdbq7n9pq2_k8440000gn/T/opencode/rec/recon3.json","w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    b.close()
