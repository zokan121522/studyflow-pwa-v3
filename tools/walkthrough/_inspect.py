#!/usr/bin/env python3
"""Inspecciona el DOM real de la app para descubrir selectores."""
import sys, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8081/"

def dump(pg, label, limit=400):
    print(f"\n{'='*70}\n## {label}\n{'='*70}")
    data = pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('*').forEach(el => {
        const t = (el.innerText||'').trim().replace(/\\s+/g,' ').slice(0,60);
        if (!t) return;
        if (el.children.length > 2) return;
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 12) return;
        let sel = el.tagName.toLowerCase();
        if (el.id) sel += '#'+el.id;
        if (el.className && typeof el.className === 'string') sel += '.'+el.className.trim().split(/\\s+/).join('.');
        for (const a of el.attributes) {
          if (a.name.startsWith('data-') || a.name === 'role' || a.name === 'aria-label' || a.name === 'placeholder')
            sel += `[${a.name}="${a.value}"]`;
        }
        const parent = el.parentElement;
        const ps = parent ? (parent.tagName.toLowerCase() + (parent.className && typeof parent.className==='string' ? '.'+parent.className.trim().split(/\\s+/).join('.') : '')) : '';
        out.push({sel, txt: t, parent: ps, y: Math.round(r.top), vis: r.top>=0 && r.top<720 && r.width>0});
      });
      return out;
    }""")
    seen = set()
    n = 0
    for d in data:
        key = d["sel"]+"|"+d["txt"]
        if key in seen: continue
        seen.add(key)
        print(f"{'V' if d['vis'] else ' '} {d['sel'][:110]}  ::  {d['txt'][:60]}")
        n += 1
        if n >= limit: 
            print("  ... (truncado)")
            break

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width":1280,"height":720}, service_workers="block")
    pg = ctx.new_page()
    pg.goto(BASE, wait_until="domcontentloaded")
    try:
        pg.wait_for_selector(".dashboard-nav-grid", state="visible", timeout=15000)
    except Exception as e:
        print("no dashboard-nav-grid:", e)
    pg.wait_for_timeout(900)
    dump(pg, "DASHBOARD (inicio)")
    pg.screenshot(path="/tmp/shot_dash.png")

    # hábitos
    pg.click('.dashboard-nav-card[data-tab="habitos"]', timeout=6000)
    pg.wait_for_timeout(1500)
    dump(pg, "HABITOS")
    pg.screenshot(path="/tmp/shot_hab.png")

    # todos
    pg.click('.dashboard-nav-card[data-tab="todos"]', timeout=6000).catch(lambda: pg.click('.tab-btn[data-tab="todos"]', timeout=6000))
    pg.wait_for_timeout(1500)
    dump(pg, "TODOS")
    pg.screenshot(path="/tmp/shot_todos.png")

    b.close()