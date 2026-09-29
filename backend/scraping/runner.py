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
import shutil
import tempfile
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

# Blocking overlays. Moodle's SCORM player asks "¿Volver a la última vista?"
# on resume, and the prompt is a full-viewport mask that swallows every
# interaction: without dismissing it the scraper never reaches the frame that
# holds the PDF and just stalls until the budget expires. The user closes it
# by clicking anywhere, so a click on the mask is the primary strategy.
_OVERLAY_SELECTORS = (
    ("css selector", ".modal.show .modal-backdrop"),
    ("css selector", ".modal-backdrop.show"),
    ("css selector", "[role='dialog']"),
    ("css selector", ".modal-dialog"),
    ("css selector", "#cmiz-0, #cmiz-1"),
    ("css selector", ".block_overlay"),
    ("css selector", ".yuimenu"),
    ("css selector", "#pn-tryagain, .pn-tryagain"),
)

# Text of the resume prompt, matched case/accent-insensitively in JS.
_OVERLAY_TEXT_HINTS = (
    "volver a la última vista",
    "volver a la ultima vista",
    "return to last view",
    "resume",
)

# Bounded budget for the dismissal loop — never stall the import.
OVERLAY_BUDGET_S = 6

# Slide-deck navigation (ported from studyflow-hub v2 scan.py). SCORM
# content is a slide deck, so the scraper walks it with the "avanzar /
# siguiente / next" button and assembles every captured page. The XPath
# text probe comes first so branded players are recognized even when the
# CSS selectors don't match.
_NEXT_BUTTON_XPATH = (
    "//button[contains(translate(normalize-space(.),"
    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'avanzar')"
    " or contains(translate(normalize-space(.),"
    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'siguiente')"
    " or contains(translate(normalize-space(.),"
    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next')]"
)
_NEXT_BUTTON_SELECTORS = (
    ("xpath", _NEXT_BUTTON_XPATH),
    ("css selector", "button[data-testid='next-button']"),
    ("css selector", "button.next"),
    ("css selector", "button[aria-label='Next']"),
    ("xpath", "//button[contains(@class,'next')]"),
)
# Bounded budget for locating the next button — never stall a slide.
SLIDES_BUDGET_S = 4.0
# Seconds to wait for a slide to render after clicking "avanzar".
SLIDE_RENDER_PAUSE_S = 1.0


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
            driver, url, course_title, output_dir, progress_cb, username, password,
            max_pages, timeout,
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
    max_pages: int = 200,
    timeout: int = 120,
) -> str:
    """Navigate, enter the SCORM frame and produce the PDF file."""
    _emit(progress_cb, f"Navegando a {url}")
    driver.get(url)
    _emit(progress_cb, "Página cargada — buscando el reproductor SCORM")
    _maybe_login(driver, username, password, progress_cb)
    _warn_if_login_wall(driver, progress_cb)
    # The "¿Volver a la última vista?" mask appears right after the player
    # loads and blocks the frame switch, so it has to go first.
    _dismiss_overlays(driver, progress_cb)
    if _switch_to_scorm_frame(driver, progress_cb):
        _emit(progress_cb, "Dentro del iframe SCORM")
    else:
        _emit(progress_cb, "Sin iframe SCORM — documento principal")
    # The player can raise the same prompt again once the frame is active.
    _dismiss_overlays(driver, progress_cb)
    pdf_path = _find_and_download_pdf(
        driver, output_dir, course_title, progress_cb, max_pages, timeout
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


# JS: walk up from the viewport centre to the OUTERMOST element that still
# covers (nearly) the whole viewport, and return it. That is the mask — not
# the dialog nested inside it. Returns null when nothing but <body> is on top,
# i.e. the content is reachable.
_COVERING_ELEMENT_JS = """
const thresh = window.innerWidth * 0.9;
const vthresh = window.innerHeight * 0.9;
const covers = (el) => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width >= thresh && r.height >= vthresh;
};
let el = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
if (!el) return null;
while (el.parentElement && covers(el.parentElement)
       && el.parentElement !== document.documentElement) {
  el = el.parentElement;
}
if (el === document.body || el === document.documentElement) return null;
return covers(el) ? el : null;
"""

# JS: what sits at a given point — used to assert the content is clickable.
_TOP_ELEMENT_AT_JS = """
const el = document.elementFromPoint(arguments[0], arguments[1]);
return el ? (el.id || el.className || el.tagName) : 'nada';
"""


def _covering_element(driver):
    """Return the topmost full-viewport element, or None if content is free."""
    from selenium.webdriver.common.by import By  # noqa: F401  (kept for parity)

    try:
        return driver.execute_script(_COVERING_ELEMENT_JS)
    except Exception:
        logger.debug("[scraper] Could not probe for a covering element")
        return None


def _dismiss_overlays(driver, progress_cb: ProgressCb = None) -> bool:
    """Close blocking overlays, the way a user would: click anywhere.

    Moodle's SCORM player raises a "¿Volver a la última vista?" mask on
    resume. It is viewport-sized and sits above the content, so the frame
    switch and the PDF lookup both stall behind it. Dismissal is attempted
    in decreasing order of politeness:

      1. press Escape — lets the page's own handler run;
      2. click an explicit "No"/"Cancelar" button when one exists;
      3. click the mask itself (what actually works on that prompt);
      4. force-hide leftovers as a last resort, so one stubborn overlay
         cannot consume the whole overlay budget.

    Returns True if something was dismissed.
    """
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    deadline = time.monotonic() + OVERLAY_BUDGET_S
    dismissed = False

    def _visible_overlay():
        """Return the first on-screen overlay element, or None."""
        for how, sel in _OVERLAY_SELECTORS:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel) if how == "css selector" else []:
                    if el.is_displayed():
                        return el
            except Exception:
                continue
        # Text-based fallback: any dialog whose text matches the prompt.
        try:
            return driver.execute_script(
                """
                const hints = arguments[0];
                const visible = (el) => {
                  const r = el.getBoundingClientRect();
                  const s = getComputedStyle(el);
                  return r.width > 0 && r.height > 0 &&
                         s.display !== 'none' && s.visibility !== 'hidden' &&
                         Number(s.opacity) > 0.05;
                };
                const norm = (s) => (s || '')
                  .normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
                for (const el of document.querySelectorAll(
                      "div,section,dialog,form")) {
                  if (!visible(el)) continue;
                  const t = norm(el.innerText);
                  if (t.length > 400) continue;              // not a dialog
                  if (hints.some((h) => t.includes(h))) return el;
                }
                return null;
                """,
                list(_OVERLAY_TEXT_HINTS),
            )
        except Exception:
            return None

    while time.monotonic() < deadline:
        overlay = _visible_overlay()
        if not overlay:
            break

        # 1. Escape — the page's own handler may close it cleanly.
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            logger.debug("[scraper] Escape did not dismiss the overlay")

        # 2. An explicit negative button, when the dialog offers one.
        try:
            for el in driver.find_elements(
                By.CSS_SELECTOR,
                ".modal.show button, [role='dialog'] button",
            ):
                label = (el.text or "").strip().lower()
                if label in {"no", "cancelar", "cerrar", "continuar", "saltar"}:
                    el.click()
                    dismissed = True
                    break
        except Exception:
            logger.debug("[scraper] No explicit dismiss button")

        # 3. Click the mask — what a user actually does on that prompt.
        try:
            ActionChains(driver).move_to_element_with_offset(overlay, 5, 5).click()
            dismissed = True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", overlay)
                dismissed = True
            except Exception:
                logger.debug("[scraper] Click on overlay failed")

        if not _visible_overlay():
            break
        time.sleep(0.4)

    # 4. Anything still standing is hidden outright, BUT only when we
    #    actually saw an overlay this round — otherwise a normal slide
    #    (#Main) or any large content div would be mistaken for one and
    #    removed.
    if dismissed:
        covering = _covering_element(driver)
        if covering:
            try:
                driver.execute_script(
                    """
                    const el = arguments[0];
                    if (el) { el.remove(); }
                    document.querySelectorAll(
                      ".modal-backdrop, .block_overlay, [role='dialog']"
                    ).forEach((n) => n.remove());
                    document.body.classList.remove('modal-open', 'overflow-hidden');
                    document.body.style.overflow = 'auto';
                    """,
                    covering,
                )
            except Exception:
                logger.debug("[scraper] Could not force-hide the overlay")

    # Only claim success once the page is provably free. Trusting the click
    # alone produced a green log while the prompt kept blocking the import.
    time.sleep(0.2)
    if dismissed and _covering_element(driver) is not None:
        logger.warning("[scraper] Overlay still blocking after dismissal")
        _emit(progress_cb, "⚠ El aviso del campus sigue bloqueando la pantalla")
        return False
    if dismissed:
        _emit(progress_cb, "Cerrado el aviso «Volver a la última vista»")
        return True
    # Nothing was ever there: free page, so no dismissal happened. Returning
    # True here would make every import look like it had hit the prompt.
    return False


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


# ─── Slide-deck capture (ported from studyflow-hub v2) ───────────────
# studyflow-hub v2 produces a real multi-page PDF by walking the SCORM
# player slide by slide: it captures the #Main element, clicks
# "avanzar/siguiente" until the button disables, and assembles every shot
# with Pillow. studyflow-hub v3 only had print-to-PDF, which captures the
# visible viewport — one image — hence this port.


def _capture_slide(driver, path: str) -> None:
    """Capture only the slide content (#Main) or fall back to full-page."""
    try:
        driver.find_element("id", "Main").screenshot(path)
        return
    except Exception:
        pass
    try:
        driver.save_screenshot(path)
    except Exception:
        logger.debug("[scraper] slide capture failed")


def _images_to_pdf(image_folder: str, output_file: str) -> str:
    """Convert every captured PNG into a single multi-page PDF (Pillow)."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover — Pillow is in the image
        raise RuntimeError("Pillow is required for the slide engine") from exc
    names = sorted(f for f in os.listdir(image_folder) if f.endswith(".png"))
    if not names:
        raise RuntimeError("No images captured to convert to PDF")
    pages = [
        Image.open(os.path.join(image_folder, n)).convert("RGB") for n in names
    ]
    pages[0].save(output_file, save_all=True, append_images=pages[1:])
    logger.info("[scraper] Slide PDF assembled: %d pages", len(pages))
    return output_file


def _find_next_button(driver, budget_s: float = SLIDES_BUDGET_S):
    """First visible 'avanzar / siguiente / next' button, or None.

    SCORM decks expose their controls as buttons whose label or class
    matches one of those words. Bounded by `budget_s` so a page without
    slides never stalls the scrape.
    """
    deadline = time.time() + budget_s
    while time.time() < deadline:
        for how, sel in _NEXT_BUTTON_SELECTORS:
            try:
                els = driver.find_elements(how, sel)
            except Exception:
                continue
            for el in els:
                try:
                    if el.is_displayed():
                        return el
                except Exception:
                    continue
        time.sleep(0.4)
    return None


def _is_button_disabled(btn) -> bool:
    """Robust disabled check — a disabled next button means the deck ended."""
    try:
        if btn.get_attribute("disabled") is not None:
            return True
        if (btn.get_attribute("aria-disabled") or "").lower() in ("true", "disabled"):
            return True
        class_attr = (btn.get_attribute("class") or "").lower()
        if "disabled" in class_attr or "cursor-not-allowed" in class_attr:
            return True
        if not btn.is_enabled():
            return True
    except Exception:
        pass
    return False


def _ensure_slides_frame(driver) -> bool:
    """Be inside the frame hosting the slide controls, if any.

    Returns True when the current context (or one of its iframes) shows a
    next button, so slide capture can proceed. Re-runs every iteration
    because player navigation can reset Chrome's frame context.
    """
    if _find_next_button(driver, budget_s=1.0):
        return True
    try:
        driver.switch_to.default_content()
        iframes = driver.find_elements("tag name", "iframe")
    except Exception:
        return False
    for fr in iframes:
        try:
            driver.switch_to.frame(fr)
            if _find_next_button(driver, budget_s=0.8):
                return True
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    return False


def _validate_pdf_page_count(path: str) -> int:
    """Open with PyMuPDF and return the page count (0 when broken)."""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # type: ignore  (older alias)
        except ImportError:
            logger.warning("[scraper] PyMuPDF unavailable — skipping validation")
            return 0
    try:
        doc = fitz.open(path)
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        return 0


def _scrape_slides_deck(
    driver,
    output_dir: str,
    course_title: str,
    max_pages: int,
    timeout: int,
    progress_cb: ProgressCb = None,
) -> Optional[str]:
    """Capture every slide of the SCORM deck into one multi-page PDF.

    Returns the PDF path, or None when no deck is present so the caller
    can fall through to the remaining strategies. This is the engine
    studyflow-hub v2 runs by default — it was the missing piece that
    previously left v3 with a single print-to-PDF image.
    """
    _emit(progress_cb, "Motor de diapositivas: capturando el reproductor…")
    shot_dir = tempfile.mkdtemp(prefix="scorm_slides_")
    start_ts = time.time()
    slide = 1
    try:
        while time.time() - start_ts < timeout:
            if slide > max_pages:
                _emit(progress_cb, f"Límite de {max_pages} diapositivas alcanzado")
                break
            if not _ensure_slides_frame(driver):
                if slide > 1:
                    break  # already captured something — deck finished
                return None  # no deck at all — let other strategies try
            # The resume prompt can recur per slide; clear it like the user.
            _dismiss_overlays(driver, progress_cb)
            shot = os.path.join(shot_dir, f"slide_{slide:04d}.png")
            _capture_slide(driver, shot)
            _emit(progress_cb, f"Diapositiva {slide} capturada")
            slide += 1

            btn = _find_next_button(driver)
            if btn is None:
                break
            if _is_button_disabled(btn):
                break
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", btn
                )
            except Exception:
                pass
            try:
                btn.click()
            except Exception:
                # A click that raises (stale element, JS guard) usually
                # means the deck ended under us — treat it as the last.
                break
            time.sleep(SLIDE_RENDER_PAUSE_S)

        captured = slide - 1
        if captured <= 0:
            return None
        pdf_path = os.path.join(output_dir, _safe_filename(course_title))
        _images_to_pdf(shot_dir, pdf_path)
        pages = _validate_pdf_page_count(pdf_path)
        if pages > 0:
            _emit(
                progress_cb,
                f"PDF multipágina listo: {pages} diapositivas → "
                f"{os.path.basename(pdf_path)}",
            )
        return pdf_path
    finally:
        try:
            shutil.rmtree(shot_dir, ignore_errors=True)
        except Exception:
            pass


def _find_and_download_pdf(
    driver,
    output_dir: str,
    course_title: str,
    progress_cb: ProgressCb = None,
    max_pages: int = 200,
    timeout: int = 120,
) -> Optional[str]:
    """Locate a PDF in the page/iframe and trigger its download.

    Strategy order: direct links, embedded viewers, then slide-deck
    capture (multi-page PDF assembled from every slide), and only as a
    last resort Chrome's print-to-PDF — which captures a single viewport.
    """
    _emit(progress_cb, "Buscando enlaces PDF en la página…")
    link = _find_pdf_link(driver)
    if link:
        return _download_via_link(driver, link, output_dir, progress_cb)

    _emit(progress_cb, "Sin enlaces directos — probando visores embebidos…")
    embed = _find_embedded_pdf(driver)
    if embed:
        return _download_via_link(driver, embed, output_dir, progress_cb)

    _emit(progress_cb, "Sin PDF enlazado — capturando diapositivas SCORM…")
    slides_pdf = _scrape_slides_deck(
        driver, output_dir, course_title, max_pages, timeout, progress_cb
    )
    if slides_pdf:
        return slides_pdf

    _emit(progress_cb, "Sin reproductor de diapositivas — usando print-to-PDF (CDP)")
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
