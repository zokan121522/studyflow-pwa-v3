#!/usr/bin/env python3
"""Recon único de toda la app: mapea cada sección (botones, inputs, vistas).

Salida: tools/walkthrough/recon_all.json
"""
import json
import os

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "recon_all.json")
BASE = "http://127.0.0.1:8081/"

Q = """(sel) => Array.from(document.querySelectorAll(sel))
  .slice(0, 24)
  .map(e => ({
    sel,
    tag: e.tagName.toLowerCase(),
    id: e.id || '',
    cls: (e.className || '').toString().slice(0, 70),
    text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 44),
    ph: e.getAttribute('placeholder') || '',
    action: e.getAttribute('data-action') || '',
    view: e.getAttribute('data-view') || '',
    tab: e.getAttribute('data-tab') || ''
  }))"""


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_context(viewport={"width": 1280, "height": 720}).new_page()
        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_selector(".dashboard-nav-grid", timeout=15000)

        tabs = pg.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              document.querySelectorAll('[data-tab]').forEach(e => {
                const t = e.getAttribute('data-tab');
                if (t && !seen.has(t)) { seen.add(t); out.push({tab:t, cls:(e.className||'').toString().slice(0,60), text:(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,40)}); }
              });
              return out;
            }"""
        )
        data = {"tabs": tabs, "sections": {}, "dashboard": {}}
        data["dashboard"] = {
            "buttons": pg.evaluate(Q, "button"),
            "inputs": pg.evaluate(Q, "input,textarea,select"),
            "cards": pg.evaluate(
                """() => Array.from(document.querySelectorAll('.dashboard-nav-card, a.dashboard-nav-card'))
                     .map(e => ({tab:e.getAttribute('data-tab'), href:e.getAttribute('href'), text:(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,60)}))"""
            ),
        }
        for t in tabs:
            tab = t["tab"]
            try:
                pg.click(f'[data-tab="{tab}"]', timeout=4000)
                pg.wait_for_timeout(700)
                sec = pg.evaluate(
                    """(tab) => {
                      const panel = document.querySelector('#view-' + tab);
                      if (!panel) return {missing: true};
                      const q = (sel) => Array.from(panel.querySelectorAll(sel)).slice(0,24).map(e => ({
                        sel, tag:e.tagName.toLowerCase(), id:e.id||'',
                        cls:(e.className||'').toString().slice(0,70),
                        text:(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,44),
                        ph:e.getAttribute('placeholder')||'',
                        action:e.getAttribute('data-action')||'',
                        view:e.getAttribute('data-view')||''
                      }));
                      return {
                        id: panel.id,
                        buttons: q('button'),
                        inputs: q('input,textarea,select'),
                        headings: Array.from(panel.querySelectorAll('h1,h2,h3')).slice(0,6).map(e=>(e.innerText||'').trim().slice(0,50)),
                        empties: Array.from(panel.querySelectorAll('.empty,.empty-state,[class*="empty"]')).slice(0,4).map(e=>(e.innerText||'').trim().slice(0,60))
                      };
                    }""",
                    tab,
                )
                data["sections"][tab] = sec
            except Exception as e:
                data["sections"][tab] = {"error": str(e)[:120]}

        json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
        print("tabs:", [t["tab"] for t in tabs])
        b.close()


if __name__ == "__main__":
    main()
