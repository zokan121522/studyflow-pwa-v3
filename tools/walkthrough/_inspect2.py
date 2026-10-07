#!/usr/bin/env python3
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081/"

def dump(pg, label, limit=500):
    print(f"\n{'='*70}\n## {label}\n{'='*70}")
    data = pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('*').forEach(el => {
        const t = (el.innerText||'').trim().replace(/\\s+/g,' ').slice(0,55);
        const r = el.getBoundingClientRect();
        if (el.children.length > 1) return;
        if (r.width < 16 || r.height < 10) return;
        let sel = el.tagName.toLowerCase();
        if (el.id) sel += '#'+el.id;
        if (el.className && typeof el.className === 'string') sel += '.'+el.className.trim().split(/\\s+/).join('.');
        for (const a of el.attributes) {
          if (a.name.startsWith('data-') || a.name === 'role' || a.name === 'aria-label' || a.name === 'placeholder' || a.name==='type')
            sel += `[${a.name}="${a.value}"]`;
        }
        out.push({sel, txt: t, vis: r.top>=0 && r.top<720 && r.left>=0 && r.left<1280 && r.width>0});
      });
      return out;
    }""")
    seen=set(); n=0
    for d in data:
        k=d["sel"]+"|"+d["txt"]
        if k in seen: continue
        seen.add(k)
        print(f"{'V' if d['vis'] else ' '} {d['sel'][:115]}  ::  {d['txt'][:55]}")
        n+=1
        if n>=limit:
            print("  ...(trunc)"); break

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width":1280,"height":720}, service_workers="block")
    pg = ctx.new_page()
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)

    # sub-pestañas de agenda
    print("### tab-btn/ht-btn data-tab en toda la app:")
    print(pg.evaluate("""() => [...document.querySelectorAll('[data-tab],[data-view],[data-subtab],[data-habit-tab],[data-action]')]
        .map(e=>e.tagName+'.'+(typeof e.className==='string'?e.className:'')+' tab='+(e.dataset.tab||'')+' view='+(e.dataset.view||'')+' sub='+(e.dataset.subtab||'')+' act='+(e.dataset.action||'')+' :: '+(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,40))"""))

    dump(pg, "SECCIÓN AGENDA (por defecto)")
    pg.screenshot(path="/tmp/a1.png", full_page=False)

    # ir a hábitos
    print("\n>>> click #dhg-view-all")
    try:
        pg.click("#dhg-view-all", timeout=4000)
    except Exception as e:
        print("err", e)
    pg.wait_for_timeout(1500)
    dump(pg, "HABITOS")
    pg.screenshot(path="/tmp/a2.png")
    b.close()