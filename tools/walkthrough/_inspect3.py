#!/usr/bin/env python3
import sys
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8081/"

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width":1280,"height":720}, service_workers="block")
    pg = ctx.new_page()
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    print("### Elementos que mencionan 'todo'/'tarea'/'pendiente':")
    print(pg.evaluate("""() => [...document.querySelectorAll('a,button,[role=button],[onclick],.tab-btn,.dashboard-nav-card,.ht-btn')]
      .map(e=>e.tagName+' .'+(typeof e.className==='string'?e.className:'')+' :: '+(e.innerText||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,50))
      .filter(t=>/todo|tarea|pendiente|check/i.test(t)).join('\\n')"""))
    print("\n### URLs cargadas / scripts con 'todo':")
    print(pg.evaluate("""() => [...document.querySelectorAll('script[src]')].map(s=>s.getAttribute('src')).filter(s=>/todo/i.test(s)).join('\\n') || '(ninguno)'"""))
    print("\n### ¿existe algo con id/class *todo* en el DOM?")
    print(pg.evaluate("""() => document.querySelectorAll('[class*=todo],[id*=todo]').length"""))
    print("\n### Ir a /todos:")
    pg.goto("http://127.0.0.1:8081/todos", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    print("url final:", pg.url)
    print(pg.evaluate("""() => [...document.querySelectorAll('[class*=todo],[id*=todo]')].map(e=>e.tagName+'#'+e.id+'.'+e.className).join('\\n') || '(nada)'"""))
    pg.screenshot(path="/tmp/todos_page.png")
    b.close()