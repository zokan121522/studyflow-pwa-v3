# backend/scorm_import.py
"""SCORM package importer (Sub-phase S7b).

Branch A (SCORM-ZIP, always available, stdlib only):
  • parse imsmanifest.xml + <resource>/<file href=*.pdf>
  • copy each PDF into the PDF store + create one pdf-ref block per PDF

Branch B (Moodle URL, gated): lazy-imports backend.scraping.runner
  when SCRAPING_ENABLED=1 AND selenium is importable. Otherwise returns
  {ok:False, reason:…} with a 200 envelope so the frontend hides the tab.

selenium is NEVER imported at module top — boot stays clean for users
without the heavy scraping stack.
"""

import os
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from backend.database import fetchone
from backend.pdf_resources import save_pdf_and_block


MAX_ZIP_SIZE = 200 * 1024 * 1024
MAX_PDFS_PER_IMPORT = 50
SCORM_NS = {
    "imscp": "http://www.imsglobal.org/xsd/imscp_v1p1",
    "adlcp": "http://www.adlnet.org/xsd/adlcp_v1p3",
    "imsmd": "http://www.imsglobal.org/xsd/imsmd_v1p2",
}


# ── Public entry ────────────────────────────────────────────────────
def import_scorm(
    user_id: int, *, url: str = "", path: str = "",
    course_id: Optional[int] = None,
    topic_id: Optional[int] = None, title: str = "",
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """Dispatch import to the right branch. Returns JSON-safe dict.

    `progress_cb` (callable(str) | None) receives short step messages that
    the SSE pseudo-terminal streams to the frontend. It is deliberately a
    local helper here — the ZIP branch must never import the scraping stack.
    """
    if path:
        return _import_scorm_zip(
            user_id, path, course_id, topic_id, title, progress_cb
        )
    if url:
        return _import_scorm_url(
            user_id, url, course_id, topic_id, title, progress_cb
        )
    return {"ok": False, "reason": "Provide either a SCORM .zip path or a Moodle URL."}


def _emit(progress_cb: Any, msg: str) -> None:
    """Push one progress line. Never raises (a listener bug must not abort)."""
    if not progress_cb:
        return
    try:
        progress_cb(msg)
    except Exception:
        pass


# ── Branch A: SCORM-ZIP ─────────────────────────────────────────────
def _import_scorm_zip(
    user_id: int, path: str,
    course_id: Optional[int], topic_id: Optional[int], title: str,
    progress_cb: Any = None,
) -> Dict[str, Any]:
    """Open the zip, parse the manifest, register every PDF resource."""
    _emit(progress_cb, f"Abriendo paquete {os.path.basename(path)}")
    preflight = _zip_preflight(path)
    if preflight:
        return preflight
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return _run_zip_import(
                user_id=user_id, path=path,
                course_id=course_id, topic_id=topic_id,
                title=title, zf=zf, progress_cb=progress_cb,
            )
    except zipfile.BadZipFile:
        return {"ok": False, "reason": "El archivo no es un ZIP válido."}
    except Exception as exc:
        return {"ok": False, "reason": f"Error leyendo ZIP: {exc}"}


def _zip_preflight(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return {"ok": False, "reason": f"ZIP no encontrado: {path}"}
    if os.path.getsize(path) > MAX_ZIP_SIZE:
        return {"ok": False, "reason": "ZIP demasiado grande (>200MB)"}
    return None


def _run_zip_import(
    *, user_id: int, path: str,
    course_id: Optional[int], topic_id: Optional[int], title: str,
    zf: zipfile.ZipFile, progress_cb: Any = None,
) -> Dict[str, Any]:
    """Parse manifest + register every PDF resource. Zip-branch driver."""
    plan = _zip_plan(zf, path, title, user_id, course_id, topic_id)
    if isinstance(plan, dict):
        return plan  # soft-error dict
    manifest_title, pdf_resources = plan
    _emit(progress_cb, f"Paquete «{manifest_title}» — "
                       f"{len(pdf_resources)} PDF(s) importables")
    _emit(progress_cb, "Registrando bloques…")
    blocks = _create_blocks_from_resources(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        zf=zf, resources=pdf_resources, manifest_title=manifest_title,
    )
    return _zip_envelope(
        manifest_title=manifest_title, blocks=blocks,
        course_id=course_id, progress_cb=progress_cb,
    )


def _zip_plan(
    zf: zipfile.ZipFile, path: str, title: str,
    user_id: int, course_id: Optional[int], topic_id: Optional[int],
) -> Any:
    """Resolve (manifest_title, pdf_resources), or a soft-error dict."""
    parsed = _read_manifest(zf, zf.namelist())
    if "ok" in parsed:
        return parsed
    manifest, resources = parsed
    if not manifest["title"]:
        manifest["title"] = title or os.path.splitext(os.path.basename(path))[0]
    pdf_resources = _pick_pdfs(resources, zf.namelist())
    if not pdf_resources:
        return {"ok": False, "reason": "El ZIP no contiene PDFs importables."}
    err = _validate_target(user_id, course_id, topic_id)
    if err:
        return err
    return manifest["title"], pdf_resources[:MAX_PDFS_PER_IMPORT]


def _zip_envelope(
    *, manifest_title: str, blocks: List[Dict[str, Any]],
    course_id: Optional[int], progress_cb: Any,
) -> Dict[str, Any]:
    """Success envelope for the ZIP branch + final terminal line."""
    if not blocks and not course_id:
        _emit(progress_cb, "Sin course_id — no se crean bloques.")
        return {
            "ok": True, "mode": "scorm-zip",
            "manifest_title": manifest_title,
            "blocks": [], "count": 0,
            "note": "Sin course_id no se crean bloques.",
        }
    _emit(progress_cb, f"✔ {len(blocks)} bloque(s) creado(s)")
    return {
        "ok": True, "mode": "scorm-zip",
        "manifest_title": manifest_title,
        "blocks": blocks, "count": len(blocks),
    }


def _read_manifest(
    zf: zipfile.ZipFile, names: List[str],
) -> Any:
    """Locate imsmanifest.xml inside the zip and parse it.

    Returns (manifest, resources) on success, else a soft-error dict.
    """
    manifest_name = None
    for n in names:
        if n.lower().endswith("imsmanifest.xml"):
            manifest_name = n
            break
    if not manifest_name:
        return {"ok": False, "reason": "No se encontró imsmanifest.xml en el ZIP."}
    try:
        xml_bytes = zf.read(manifest_name)
    except Exception as exc:
        return {"ok": False, "reason": f"No se pudo leer el manifest: {exc}"}
    return parse_manifest(xml_bytes)


def _pick_pdfs(
    resources: List[Dict[str, str]], names: List[str]
) -> List[Dict[str, str]]:
    """Filter manifest resources for PDFs; fall back to scanning the archive."""
    from_manifest = [r for r in resources if r["href"].lower().endswith(".pdf")]
    if from_manifest:
        return from_manifest
    return [
        {"title": os.path.splitext(os.path.basename(n))[0],
         "href": n, "item_title": ""}
        for n in names
        if n.lower().endswith(".pdf") and not n.endswith("/")
    ]


def _validate_target(
    user_id: int, course_id: Optional[int], topic_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    if course_id and not _own_course(user_id, course_id):
        return {"ok": False, "reason": "Curso no encontrado"}
    if topic_id and not _own_topic(user_id, course_id, topic_id):
        return {"ok": False, "reason": "Tema no encontrado"}
    return None


def parse_manifest(
    xml_bytes: bytes,
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Extract {title, items} + resource list from a SCORM manifest."""
    manifest: Dict[str, Any] = {"title": "", "items": []}
    resources: List[Dict[str, str]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return manifest, resources

    # Course title (imsmanifest → imscp:organization → imscp:title)
    title_el = root.find(".//imscp:organization/imscp:title", SCORM_NS)
    if title_el is None:
        title_el = root.find(".//imscp:title", SCORM_NS)
    if title_el is not None and title_el.text:
        manifest["title"] = title_el.text.strip()

    # Items — collect identifierref for later lookup
    for item in root.findall(".//imscp:item", SCORM_NS):
        identifierref = item.get("identifierref") or ""
        title_node = item.find("imscp:title", SCORM_NS)
        item_title = (title_node.text or "").strip() if title_node is not None else ""
        if identifierref:
            manifest["items"].append({"identifierref": identifierref, "title": item_title})

    # Resources: <resources><resource><file href="..."/>...
    item_title_by_ref = {it["identifierref"]: it["title"] for it in manifest["items"]}
    for res in root.findall(".//imscp:resource", SCORM_NS):
        rid = res.get("identifier") or ""
        item_title = item_title_by_ref.get(rid, "")
        for f in res.findall("imscp:file", SCORM_NS):
            href = (f.get("href") or "").strip()
            if href and not href.lower().endswith(".zip"):
                resources.append({"href": href, "title": "", "item_title": item_title})
    return manifest, resources


def _create_blocks_from_resources(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    zf: zipfile.ZipFile, resources: List[Dict[str, str]], manifest_title: str,
) -> List[Dict[str, Any]]:
    """Copy each PDF into the upload folder + register + create a block."""
    created: List[Dict[str, Any]] = []
    for r in resources:
        result = _register_zip_resource(
            user_id=user_id, course_id=course_id, topic_id=topic_id,
            zf=zf, resource=r, manifest_title=manifest_title,
        )
        if result:
            created.append(result)
    return created


def _register_zip_resource(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    zf: zipfile.ZipFile, resource: Dict[str, str], manifest_title: str,
) -> Optional[Dict[str, Any]]:
    """Read ONE resource from the zip, store it, create its block."""
    href = resource["href"]
    try:
        data = zf.read(href)
    except (KeyError, Exception):
        return None
    safe_name = os.path.basename(href)
    title = (resource.get("item_title") or resource.get("title")
             or os.path.splitext(safe_name)[0] or manifest_title)
    return save_pdf_and_block(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        data=data, original_name=safe_name, title=title,
    )


# ── Branch B: Moodle URL (gated to selenium stack) ──────────────────
def _import_scorm_url(
    user_id: int, url: str,
    course_id: Optional[int], topic_id: Optional[int], title: str,
    progress_cb: Any = None,
) -> Dict[str, Any]:
    """Run the v2 selenium scraper if it's both enabled AND importable."""
    gate_error = _scraping_gate_error()
    if gate_error:
        _emit(progress_cb, f"❌ {gate_error['reason']}")
        return gate_error
    if not _own_course(user_id, course_id):
        _emit(progress_cb, "❌ Curso no encontrado.")
        return {"ok": False, "reason": "Curso no encontrado."}
    pdf_path = _run_scraper(url, title, progress_cb)
    if isinstance(pdf_path, str) and pdf_path.startswith("error: "):
        reason = pdf_path[len("error: "):] or "Scraping falló."
        _emit(progress_cb, f"❌ {reason}")
        return {"ok": False, "reason": reason}
    if not isinstance(pdf_path, str) or not os.path.isfile(pdf_path):
        _emit(progress_cb, "❌ El scraper no produjo ningún PDF.")
        return {"ok": False, "reason": "Scraping no produjo PDF."}
    _emit(progress_cb, "Registrando el PDF scrapeado…")
    return _import_scrape_result(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        pdf_path=pdf_path, title=title, progress_cb=progress_cb,
    )


def _scraping_gate_error() -> Optional[Dict[str, Any]]:
    if not _scraping_runtime_available():
        return {
            "ok": False,
            "reason": ("Scraping Moodle no disponible en este equipo "
                       "(instalar selenium/Chrome o activar SCRAPING_ENABLED)."),
        }
    try:
        from backend.scraping.runner import run_scrape  # noqa: F401
    except ImportError:
        return {"ok": False, "reason": "Falta backend.scraping.runner."}
    return None


def _run_scraper(url: str, title: str, progress_cb: Any = None) -> Optional[str]:
    """Run the Selenium scraper, returning the PDF path or "error: …"."""
    from backend.scraping.runner import run_scrape  # type: ignore
    scrape_dir = os.environ.get("SCRAPED_DIR", "/tmp/scraped_pdfs")
    os.makedirs(scrape_dir, exist_ok=True)
    try:
        return run_scrape(
            url=url, course_title=title or "scorm_export",
            output_dir=scrape_dir, max_pages=200, timeout=120,
            progress_cb=progress_cb,
        )
    except Exception as exc:
        return f"error: {exc}"


def _import_scrape_result(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    pdf_path: str, title: str, progress_cb: Any = None,
) -> Dict[str, Any]:
    """Take the scraper's PDF file and register it as a pdf-ref block."""
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        _emit(progress_cb, f"❌ No se pudo leer el PDF: {exc}")
        return {"ok": False, "reason": f"No se pudo leer PDF: {exc}"}
    original_name = os.path.basename(pdf_path)
    record = save_pdf_and_block(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        data=data, original_name=original_name, title=title or original_name,
    )
    if not record:
        _emit(progress_cb, "❌ No se pudo registrar el PDF scrapeado.")
        return {"ok": False, "reason": "No se pudo registrar el PDF scrapeado."}
    _emit(progress_cb, f"✔ Bloque creado: {title or original_name}")
    return {"ok": True, "mode": "moodle-scrape", "blocks": [record], "count": 1}


# ── Capability probes (used by /import/status) ─────────────────────
def is_scorm_zip_available() -> bool:
    """SCORM-ZIP only needs Python stdlib → always true."""
    return True


def is_moodle_scraping_available() -> bool:
    """True only when both env flag is on AND selenium imports cleanly."""
    if os.environ.get("SCRAPING_ENABLED") != "1":
        return False
    try:
        import selenium  # noqa: F401
        return True
    except Exception:
        return False


def _scraping_runtime_available() -> bool:
    if os.environ.get("SCRAPING_ENABLED") != "1":
        return False
    try:
        import selenium  # noqa: F401
        from selenium import webdriver  # noqa: F401
        return True
    except Exception:
        return False


# ── DB helpers ──────────────────────────────────────────────────────
def _own_course(user_id: int, course_id: Optional[int]) -> bool:
    if not course_id:
        return False
    row = fetchone(
        "SELECT 1 FROM courses WHERE id = %s AND user_id = %s",
        (course_id, user_id),
    )
    return bool(row)


def _own_topic(user_id: int, course_id: Optional[int], topic_id: int) -> bool:
    if not topic_id:
        return False
    row = fetchone(
        "SELECT 1 FROM topics WHERE id = %s AND course_id = %s AND user_id = %s",
        (topic_id, course_id, user_id),
    )
    return bool(row)
