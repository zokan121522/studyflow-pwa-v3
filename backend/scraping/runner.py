# backend/scraping/runner.py
"""Moodle SCORM → PDF scraper (Selenium headless).

Exports a single function `run_scrape` matching the signature expected by
scorm_import._run_scraper.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def run_scrape(
    url: str,
    course_title: str,
    output_dir: str,
    max_pages: int = 200,
    timeout: int = 120,
) -> str:
    """
    Open Chrome headless, navigate to the Moodle SCORM URL, find and download
    the PDF, and return the absolute path to the local PDF file.

    Raises:
        RuntimeError: If Chrome/selenium setup fails or PDF not found.
        Exception:    Propagates any unexpected selenium errors.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException

    os.makedirs(output_dir, exist_ok=True)

    # ── Chrome options for headless scraping ──────────────────────────
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-sync")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--disable-popup-blocking")
    # Allow downloads
    prefs = {
        "download.default_directory": output_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,  # Download PDF instead of viewing
    }
    chrome_options.add_experimental_option("prefs", prefs)

    # Let Selenium Manager (built into selenium 4.6+) auto-download matching chromedriver
    service = Service()

    driver = None
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(timeout)

        logger.info(f"[scraper] Navigating to {url}")
        driver.get(url)

        # Wait for page to load - try multiple selectors common in Moodle SCORM
        wait = WebDriverWait(driver, timeout)

        # Moodle SCORM player often loads in an iframe
        _switch_to_scorm_frame(driver, wait)

        # Try to find a PDF link or embed
        pdf_path = _find_and_download_pdf(driver, wait, output_dir, course_title)

        if pdf_path and os.path.isfile(pdf_path):
            logger.info(f"[scraper] PDF downloaded to {pdf_path}")
            return pdf_path

        raise RuntimeError("No PDF found in SCORM package")

    except WebDriverException as e:
        logger.error(f"[scraper] WebDriver error: {e}")
        raise RuntimeError(f"Chrome/selenium error: {e}") from e
    except TimeoutException as e:
        logger.error(f"[scraper] Timeout: {e}")
        raise RuntimeError(f"Page load timeout: {e}") from e
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _switch_to_scorm_frame(driver, wait: "WebDriverWait") -> bool:
    """Try to switch into the SCORM player iframe if present."""
    try:
        # Common Moodle SCORM iframe selectors
        iframe_selectors = [
            (By.TAG_NAME, "iframe"),
            (By.CSS_SELECTOR, "iframe[name*='scorm']"),
            (By.CSS_SELECTOR, "iframe[id*='scorm']"),
            (By.CSS_SELECTOR, "iframe[src*='scorm']"),
            (By.CSS_SELECTOR, "#scorm_iframe"),
            (By.CSS_SELECTOR, ".scorm-player iframe"),
        ]
        for by, sel in iframe_selectors:
            try:
                iframe = wait.until(EC.presence_of_element_located((by, sel)))
                driver.switch_to.frame(iframe)
                logger.debug(f"[scraper] Switched to iframe: {by}={sel}")
                return True
            except TimeoutException:
                continue
    except Exception:
        pass
    return False


def _find_and_download_pdf(
    driver, wait: "WebDriverWait", output_dir: str, course_title: str
) -> Optional[str]:
    """Locate a PDF in the page/iframe and trigger its download."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

    # Strategy 1: Direct PDF link (<a href="...pdf">)
    pdf_link_selectors = [
        (By.CSS_SELECTOR, "a[href$='.pdf']"),
        (By.CSS_SELECTOR, "a[href*='.pdf']"),
        (By.XPATH, "//a[contains(@href, '.pdf')]"),
        (By.CSS_SELECTOR, "a[download][href*='.pdf']"),
    ]
    for by, sel in pdf_link_selectors:
        try:
            links = driver.find_elements(by, sel)
            for link in links:
                href = link.get_attribute("href")
                if href and href.lower().endswith(".pdf"):
                    return _download_via_link(driver, href, output_dir, course_title)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    # Strategy 2: Embedded PDF viewer (object/embed/iframe with PDF src)
    pdf_embed_selectors = [
        (By.CSS_SELECTOR, "object[data$='.pdf']"),
        (By.CSS_SELECTOR, "embed[src$='.pdf']"),
        (By.CSS_SELECTOR, "iframe[src$='.pdf']"),
        (By.XPATH, "//object[contains(@data, '.pdf')]"),
        (By.XPATH, "//embed[contains(@src, '.pdf')]"),
        (By.XPATH, "//iframe[contains(@src, '.pdf')]"),
    ]
    for by, sel in pdf_embed_selectors:
        try:
            el = wait.until(EC.presence_of_element_located((by, sel)))
            src = el.get_attribute("data") or el.get_attribute("src")
            if src and src.lower().endswith(".pdf"):
                return _download_via_link(driver, src, output_dir, course_title)
        except TimeoutException:
            continue
        except Exception:
            continue

    # Strategy 3: Print to PDF (fallback - capture the whole page as PDF)
    # This requires Chrome's headless print-to-PDF capability
    try:
        return _print_page_to_pdf(driver, output_dir, course_title)
    except Exception as e:
        logger.warning(f"[scraper] Print-to-PDF fallback failed: {e}")

    return None


def _download_via_link(
    driver, pdf_url: str, output_dir: str, course_title: str
) -> Optional[str]:
    """Navigate directly to PDF URL to trigger download."""
    import urllib.parse

    logger.info(f"[scraper] Downloading PDF from {pdf_url}")
    driver.get(pdf_url)

    # Wait for download to complete (Chrome downloads to output_dir)
    # Poll for a new .pdf file in output_dir
    start = time.time()
    timeout = 30
    seen_files = set(Path(output_dir).glob("*.pdf"))

    while time.time() - start < timeout:
        current_files = set(Path(output_dir).glob("*.pdf"))
        new_files = current_files - seen_files
        if new_files:
            # Return the newest/largest PDF file
            latest = max(new_files, key=lambda f: f.stat().st_size)
            if latest.stat().st_size > 1024:  # At least 1KB
                return str(latest.absolute())
        time.sleep(0.5)

    # If no new file detected, check if the current page IS the PDF
    # (Chrome headless sometimes renders PDF inline)
    current_files = list(Path(output_dir).glob("*.pdf"))
    if current_files:
        latest = max(current_files, key=lambda f: f.stat().st_mtime)
        if latest.stat().st_size > 1024:
            return str(latest.absolute())

    return None


def _print_page_to_pdf(
    driver, output_dir: str, course_title: str
) -> Optional[str]:
    """Use Chrome DevTools Protocol to print the current page as PDF."""
    import base64
    import json

    logger.info("[scraper] Using print-to-PDF fallback")

    # Sanitize filename
    safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in course_title)
    safe_title = safe_title.strip()[:100] or "scorm_export"
    pdf_filename = f"{safe_title}.pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)

    # CDP command: Page.printToPDF
    result = driver.execute_cdp_cmd("Page.printToPDF", {
        "landscape": False,
        "displayHeaderFooter": False,
        "printBackground": True,
        "preferCSSPageSize": True,
    })

    pdf_base64 = result.get("data")
    if not pdf_base64:
        raise RuntimeError("CDP printToPDF returned no data")

    pdf_bytes = base64.b64decode(pdf_base64)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    if os.path.getsize(pdf_path) < 1024:
        raise RuntimeError("Generated PDF too small")

    return pdf_path