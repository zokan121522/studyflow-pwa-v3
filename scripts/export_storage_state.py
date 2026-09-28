#!/usr/bin/env python3
"""
Export Chrome cookies for NotebookLM -- without Playwright.

Reads cookies directly from Chrome's SQLite database using browser-cookie3.
No browser is opened -- it just reads the cookie file and closes.

Usage:
    python3 export_storage_state.py

Prerequisites:
    pip3 install browser-cookie3

What it does:
    1. Closes Chrome (to release the SQLite database lock)
    2. Reads Google auth cookies from Chrome's cookie database
    3. Saves storage_state.json to ~/.notebooklm/profiles/{email}/
    4. The file is immediately visible to the PWA backend (local macOS)
"""

import json
import os
import subprocess
import sys
import time

# -- THIS VALUE IS REPLACED BY THE BACKEND WHEN YOU DOWNLOAD THE SCRIPT --
ACCOUNT_EMAIL = "{{EMAIL}}"
# ------------------------------------------------------------------------


def close_chrome() -> None:
    """Close Google Chrome gracefully to release the SQLite database lock."""
    print("Cerrando Chrome...")
    subprocess.run(
        ["osascript", "-e", 'tell application "Google Chrome" to quit'],
        capture_output=True,
    )
    for _ in range(30):
        ret = subprocess.run(
            ["pgrep", "-x", "Google Chrome"],
            capture_output=True,
        )
        if ret.returncode != 0:
            print("   Chrome cerrado")
            return
        time.sleep(0.5)
    print("   Chrome no se cerro -- se intentara igualmente...")


def main() -> None:
    # -- Check browser_cookie3 ------------------------------------------
    try:
        import browser_cookie3
    except ImportError:
        print("Falta el paquete 'browser-cookie3'.")
        print()
        print("   Instalalo con:")
        print("   pip3 install browser-cookie3")
        print()
        sys.exit(1)

    # -- Validate email -------------------------------------------------
    if "@" not in ACCOUNT_EMAIL:
        print("El email de la cuenta no esta configurado.")
        print()
        print("   Descarga el script DESDE EL PANEL DE NOTEBOOKLM")
        print("   introduciendo tu email en el campo correspondiente.")
        print()
        sys.exit(1)

    # -- Close Chrome to release SQLite lock ----------------------------
    close_chrome()
    time.sleep(0.5)

    # -- Read cookies ---------------------------------------------------
    print(f"Cuenta: {ACCOUNT_EMAIL}")
    print("Leyendo cookies de Google Chrome...")

    cj = browser_cookie3.chrome(domain_name="google.com")

    cookies = []
    for c in cj:
        cookies.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
            "expires": int(c.expires) if c.expires else -1,
            "httpOnly": c.name.startswith("__Secure-") or c.name.startswith("__Host-"),
            "secure": getattr(c, "secure", False),
            "sameSite": "Lax",
        })

    if not cookies:
        print("No se encontraron cookies de Google en Chrome.")
        print()
        print("   Asegurate de haber iniciado sesion en Google DESDE Chrome")
        print("   con la cuenta que quieres usar en NotebookLM.")
        print()
        sys.exit(1)

    # -- Build storage_state.json ---------------------------------------
    storage = {
        "cookies": cookies,
        "origins": [],
        "notebooklm": {
            "account": {
                "email": ACCOUNT_EMAIL,
            },
        },
    }

    output_dir = os.path.expanduser(f"~/.notebooklm/profiles/{ACCOUNT_EMAIL}")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "storage_state.json")

    with open(output_path, "w") as f:
        json.dump(storage, f, indent=2, ensure_ascii=False)

    print(f"\nCookies exportadas: {output_path}")
    print(f"   Cookies capturadas: {len(cookies)}")
    print()
    print("Ve al panel de NotebookLM y pulsa 'Buscar nuevo perfil'.")


if __name__ == "__main__":
    main()