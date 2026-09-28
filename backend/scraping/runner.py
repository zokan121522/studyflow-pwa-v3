# backend/scraping/runner.py
"""Moodle SCORM → PDF scraper (Selenium headless).

Exports `run_scrape` — the signature expected by scorm_import._run_scraper —
plus an optional `progress_cb`: a callable(str) invoked with short,
human-readable step messages. The SSE pseudo-terminal (S7b-A) forwards those
to the frontend; with `progress_cb` omitted the scraper behaves exactly as
before, and a listener that raises never breaks the scrape.

selenium is imported lazily inside each helper so the app boots clean for
users without the scraping stack.
"""

import os
import time
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]

# Headless Chrome flags — identical set to the pre-streaming runner.
_CHROME_ARGS = (
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-default-apps",
    "--no-first-run",
    "--disable-popup-blocking",
)

# Bounded polling budgets.
#
# The pre-streaming runner waited `WebDriverWait(driver, 120)` PER SELECTOR:
# 6 iframe selectors + 6 embed selectors = up to 24 minutes of silent hanging
# when the page had neither. Polling with a global budget keeps the same
# discovery logic while capping the stall at FRAME_BUDGET_S + EMBED_BUDGET_S.
FRAME_BUDGET_S = 20
EMBED_BUDGET_S = 10

# Locators are spelled out as selenium `By` string values ("tag name",
# "css selector", "xpath") so this module never imports selenium at top level.
_IFRAME_SELECTORS = (
    ("tag name", "iframe"),
    ("css selector", "iframe[name*='scorm']"),
    ("css selector", "iframe[id*='scorm']"),
    ("css selector", "iframe[src*='scorm']"),
    ("css selector", "#scorm_iframe"),
    ("css selector", ".scorm-player iframe"),
)

_EMBED_SELECTORS = (
    ("css selector", "object[data$='.pdf']"),
    ("css selector", "embed[src$='.pdf']"),
    ("css selector", "iframe[src$='.pdf']"),
    ("xpath", "//object[contains(@data, '.pdf')]"),
    ("xpath", "//embed[contains(@src, '.pdf')]"),
    ("xpath", "//iframe[contains(@src, '.pdf')]"),
)


def _emit(progress_cb: ProgressCb, msg: str) -> None:
    """Safely invoke the optional progress callback. Never raises."""
    if not progress_cb:
        return
    try:
        progress_cb(msg)
    except Exception as exc:  # pragma: no cover — listener bug
        logger.debug("runner progress_cb raised (ignored): %s", exc)


# ─── Moodle login ────────────────────────────────────────────────────
# Selectors for the standard Moodle login form (login/index.php).
_LOGIN_USER_SELECTORS = (
    ("css selector", "input[name='username']"),
    ("css selector", "#loginusername"),
    ("css selector", "input[type='text']"),
)
_LOGIN_PASS_SELECTOR = ("css selector", "input[type='password']")
_LOGIN_SUBMIT_SELECTORS = (
    ("css selector", "button[type='submit']"),
    ("css selector", "input[type='submit']"),
    ("css selector", "form.mform button"),
)
LOGIN_BUDGET_S = 20


def _login_field(driver, selectors):
    """First element matching any selector, or None."""
    for by, sel in selectors:
        try:
            found = _poll_locate(driver, ((by, sel),), 2.0, _first)
        except Exception:
            continue
        if found:
            return found
    return None


def _is_login_page(driver) -> bool:
    """True if the browser is sitting on a login form."""
    if "/login" in (driver.current_url or "").lower():
        return True
    try:
        return bool(driver.find_elements(*_LOGIN_PASS_SELECTOR))
    except Exception:
        return False


def _attempt_login(driver, username, password, progress_cb) -> bool:
    """Fill and submit the Moodle login form. Returns True on success."""
    _emit(progress_cb, "Formulario de login detectado — iniciando sesión…")
    user_el = _login_field(driver, _LOGIN_USER_SELECTORS)
    pass_el = _login_field(driver, (_LOGIN_PASS_SELECTOR,))
    if not (user_el and pass_el):
        _emit(progress_cb, "⚠ No se pudo rellenar el login (formulario raro)")
        return False
    try:
        user_el.clear()
        user_el.send_keys(username)
        pass_el.clear()
        pass_el.send_keys(password)
        pass_el.submit()
    except Exception as exc:
        logger.warning("[scraper] login fill failed: %s", exc)
        _emit(progress_cb, f"⚠ Login falló al escribir: {exc}")
        return False
    # Wait (bounded) for the URL to leave /login.
    deadline = time.time() + LOGIN_BUDGET_S
    while time.time() < deadline:
        if "/login" not in (driver.current_url or "").lower():
            _emit(progress_cb, "Sesión iniciada correctamente ✓")
            return True
        time.sleep(0.5)
    _emit(progress_cb, "⚠ El login no redirigió (¿credenciales incorrectas?)")
    return False


def _maybe_login(driver, username, password, progress_cb) -> None:
    """Log into Moodle when a login form is present and creds are available."""
    if not (username and password):
        return
    if not _is_login_page(driver):
        return
    if _attempt_login(driver, username, password, progress_cb):
        return
    # After a failed login the browser stays on the form; nothing else to do.


def _resolve_credentials(username, password):
    """Credentials priority: explicit args > SCRAPING_* env vars."""
    user = (username or "").strip() or (os.environ.get("SCRAPING_USERNAME") or "").strip()
    pwd = password or os.environ.get("SCRAPING_PASSWORD") or ""
    return (user or None, pwd or None)


def run_scrape(
    url: str,
    course_title: str,
    output_dir: str,
    max_pages: int = 200,
    timeout: int = 120,
    progress_cb: ProgressCb = None,
    username: str = None,
    password: str = None,
) -> str:
    """Open Chrome headless, scrape `url` and return the local PDF path.

    Emits one progress message per stage through `progress_cb`. Raises
    RuntimeError when Chrome fails or no PDF can be produced.
    """
    from selenium.common.exceptions import TimeoutException, WebDriverException

    os.makedirs(output_dir, exist_ok=True)
    _emit(progress_cb, "Iniciando Chrome headless…")
    driver = _open_driver(timeout, output_dir, progress_cb)
    username, password = _resolve_credentials(username, password)
    try:
        return _scrape(
            driver, url, course_title, output_dir, progress_cb, username, password
        )
    except WebDriverException as exc:
        logger.error("[scraper] WebDriver error: %s", exc)
        _emit(progress_cb, f"❌ Chrome/Selenium falló: {exc}")
        raise RuntimeError(f"Chrome/selenium error: {exc}") from exc
    except TimeoutException as exc:
        logger.error("[scraper] Timeout: %s", exc)
        _emit(progress_cb, f"❌ Tiempo de espera agotado: {exc}")
        raise RuntimeError(f"Page load timeout: {exc}") from exc
    finally:
        _quit_driver(driver)


def _scrape(
    driver,
    url: str,
    course_title: str,
    output_dir: str,
    progress_cb: ProgressCb,
    username: str = None,
    password: str = None,
) -> str:
    """Navigate, enter the SCORM frame and produce the PDF file."""
    _emit(progress_cb, f"Navegando a {url}")
    driver.get(url)
    _emit(progress_cb, "Página cargada — buscando el reproductor SCORM")
    _maybe_login(driver, username, password, progress_cb)
    _warn_if_login_wall(driver, progress_cb)
    if _switch_to_scorm_frame(driver, progress_cb):
        _emit(progress_cb, "Dentro del iframe SCORM")
    else:
        _emit(progress_cb, "Sin iframe SCORM — documento principal")
    pdf_path = _find_and_download_pdf(
        driver, output_dir, course_title, progress_cb
    )
    if not (pdf_path and os.path.isfile(pdf_path)):
        raise RuntimeError("No PDF found in SCORM package")
    size_kb = os.path.getsize(pdf_path) // 1024
    _emit(progress_cb, f"PDF listo: {os.path.basename(pdf_path)} ({size_kb} KB)")
    return pdf_path


def _warn_if_login_wall(driver, progress_cb: ProgressCb) -> None:
    """Announce a login redirect so the terminal tells the truth.

    Moodle sends unauthenticated scrapes to /login/index.php. The scrape
    still "succeeds" (print-to-PDF of the login form) without this hint,
    which is exactly how four junk PDFs got imported before it existed.
    """
    final_url = (driver.current_url or "").lower()
    if "/login" in final_url:
        _emit(
            progress_cb,
            "⚠ Redirigido a la página de login — sin credenciales el PDF "
            f"solo contendrá el formulario ({driver.current_url})",
        )


def _download_prefs(output_dir: str) -> dict:
    """Chrome prefs: save downloads into output_dir and open PDFs externally."""
    return {
        "download.default_directory": output_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    }


def _open_driver(timeout: int, output_dir: str, progress_cb: ProgressCb):
    """Build the headless Chrome WebDriver (Selenium Manager resolves driver)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    for arg in _CHROME_ARGS:
        options.add_argument(arg)
    options.add_experimental_option("prefs", _download_prefs(output_dir))
    driver = webdriver.Chrome(service=Service(), options=options)
    driver.set_page_load_timeout(timeout)
    _emit(progress_cb, "Chrome listo — esperando la página")
    return driver


def _quit_driver(driver) -> None:
    """Close Chrome if it was opened. Never raises."""
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _poll_locate(driver, selectors, budget_s: float, pick):
    """Poll `selectors` until `pick(elements)` returns a value or time's up.

    Replaces `WebDriverWait(driver, 120)` per selector (up to 24 minutes of
    silent hanging when nothing matched) with a single global budget.
    """
    deadline = time.time() + budget_s
    while time.time() < deadline:
        for by, sel in selectors:
            try:
                found = pick(driver.find_elements(by, sel))
            except Exception:
                continue
            if found:
                return found
        time.sleep(0.5)
    return None


def _first(elements):
    """pick() helper: first element of the match list, or None."""
    return elements[0] if elements else None


def _switch_to_scorm_frame(driver, progress_cb: ProgressCb = None) -> bool:
    """Try to switch into the SCORM player iframe if present."""
    _emit(progress_cb, "Buscando el iframe del reproductor SCORM…")
    iframe = _poll_locate(driver, _IFRAME_SELECTORS, FRAME_BUDGET_S, _first)
    if not iframe:
        return False
    try:
        driver.switch_to.frame(iframe)
        logger.debug("[scraper] Switched to SCORM iframe")
        return True
    except Exception:
        return False


def _find_and_download_pdf(
    driver, output_dir: str, course_title: str, progress_cb: ProgressCb = None
) -> Optional[str]:
    """Locate a PDF in the page/iframe and trigger its download."""
    _emit(progress_cb, "Buscando enlaces PDF en la página…")
    link = _find_pdf_link(driver)
    if link:
        return _download_via_link(driver, link, output_dir, progress_cb)

    _emit(progress_cb, "Sin enlaces directos — probando visores embebidos…")
    embed = _find_embedded_pdf(driver)
    if embed:
        return _download_via_link(driver, embed, output_dir, progress_cb)

    _emit(progress_cb, "Sin visores embebidos — usando print-to-PDF (CDP)")
    try:
        return _print_page_to_pdf(driver, output_dir, course_title, progress_cb)
    except Exception as exc:
        logger.warning("[scraper] Print-to-PDF fallback failed: %s", exc)
        _emit(progress_cb, f"❌ print-to-PDF falló: {exc}")
        return None


def _find_pdf_link(driver) -> Optional[str]:
    """Strategy 1: an <a> whose href points at a .pdf file."""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import StaleElementReferenceException

    selectors = [
        (By.CSS_SELECTOR, "a[href$='.pdf']"),
        (By.CSS_SELECTOR, "a[href*='.pdf']"),
        (By.XPATH, "//a[contains(@href, '.pdf')]"),
        (By.CSS_SELECTOR, "a[download][href*='.pdf']"),
    ]
    for by, sel in selectors:
        try:
            for link in driver.find_elements(by, sel):
                href = link.get_attribute("href")
                if href and href.lower().endswith(".pdf"):
                    return href
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    return None


def _find_embedded_pdf(driver) -> Optional[str]:
    """Strategy 2: object/embed/iframe whose src/data points at a .pdf."""

    def pick(elements):
        for el in elements:
            src = el.get_attribute("data") or el.get_attribute("src")
            if src and src.lower().endswith(".pdf"):
                return src
        return None

    return _poll_locate(driver, _EMBED_SELECTORS, EMBED_BUDGET_S, pick)


def _newest_pdf(files, min_size: int = 1024) -> Optional[Path]:
    """Newest PDF in `files` above `min_size` bytes, or None."""
    candidates = [f for f in files if f.stat().st_size > min_size]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def _download_via_link(
    driver, pdf_url: str, output_dir: str, progress_cb: ProgressCb = None
) -> Optional[str]:
    """Navigate directly to the PDF URL and wait for Chrome to save it."""
    _emit(progress_cb, f"Descargando {pdf_url}")
    driver.get(pdf_url)

    start = time.time()
    deadline = start + 30
    seen_files = set(Path(output_dir).glob("*.pdf"))
    next_beat = 5

    while time.time() < deadline:
        elapsed = int(time.time() - start)
        if elapsed >= next_beat:  # keep the terminal (and proxies) alive
            _emit(progress_cb, f"Esperando descarga… {elapsed}s")
            next_beat += 5
        fresh = set(Path(output_dir).glob("*.pdf")) - seen_files
        latest = _newest_pdf(fresh)
        if latest:
            _emit(progress_cb, f"Descarga completada ({latest.stat().st_size // 1024} KB)")
            return str(latest.absolute())
        time.sleep(0.5)

    # Chrome sometimes renders the PDF inline instead of saving it.
    latest = _newest_pdf(set(Path(output_dir).glob("*.pdf")))
    if latest:
        _emit(progress_cb, f"PDF recogido del visor ({latest.stat().st_size // 1024} KB)")
        return str(latest.absolute())
    return None


def _print_page_to_pdf(
    driver, output_dir: str, course_title: str, progress_cb: ProgressCb = None
) -> Optional[str]:
    """Strategy 3: render the current page with Chrome's print-to-PDF."""
    _emit(progress_cb, "Generando PDF con print-to-PDF…")
    pdf_path = os.path.join(output_dir, _safe_filename(course_title))
    pdf_bytes = _cdp_print_pdf(driver)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    if os.path.getsize(pdf_path) < 1024:
        raise RuntimeError("Generated PDF too small")
    logger.info("[scraper] Using print-to-PDF fallback")
    return pdf_path


def _safe_filename(course_title: str) -> str:
    """Turn the course title into a filesystem-safe .pdf name."""
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in course_title)
    return (safe.strip()[:100] or "scorm_export") + ".pdf"


def _cdp_print_pdf(driver) -> bytes:
    """Issue CDP Page.printToPDF and return the raw PDF bytes."""
    import base64

    result = driver.execute_cdp_cmd("Page.printToPDF", {
        "landscape": False,
        "displayHeaderFooter": False,
        "printBackground": True,
        "preferCSSPageSize": True,
    })
    pdf_base64 = result.get("data")
    if not pdf_base64:
        raise RuntimeError("CDP printToPDF returned no data")
    return base64.b64decode(pdf_base64)
