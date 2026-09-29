"""
Utility functions for AI content generation — v3 port.

PDF path resolution adapted from v2 ``backend/ai/utils.py`` to the v3
filesystem layout:

- Uploaded PDFs live in the v3 uploads folder (``PDF_UPLOAD_FOLDER`` env
  or ``<repo>/uploads/pdfs`` — same source as ``routes/pdf.py``).
- ``pdfsCurso`` curriculum PDFs live under the local StudyFlow app dir
  (``~/.studyflow-app/roadmap/pdfsCurso``).

Only the module docstring keeps the v2 name for traceability. The heavy
HTML template / PDF text filtering helpers of v2 are left out: they
belong to the OpenZEN (generation) family, deferred to its own sub-phase.
"""

import os
from pathlib import Path

# ─── PDF store directories (v3 layout) ─────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Uploaded PDFs (matches backend/routes/pdf.py UPLOAD_FOLDER resolution).
PDF_DATA_DIR = os.environ.get(
    "PDF_UPLOAD_FOLDER",
    str(_REPO_ROOT / "uploads" / "pdfs"),
)

# Curriculum PDFs (pdfsCurso) — local StudyFlow app dir.
PDFSCURSO_DIR = os.environ.get(
    "PDFSCURSO_DIR",
    str(Path.home() / ".studyflow-app" / "roadmap" / "pdfsCurso"),
)


# ─── Helper: Resolve PDF path ──────────────────────────────────────


def _resolve_pdf_path(pdf_path: str) -> str | None:
    """Resolve a pdf_path from the blocks table to an absolute filesystem path.

    Search order:
      0. API URL path (/api/pdf/<id> or /api/pdf/serve/<filename>) → DB / upload
      1. Uploaded PDFs store  (PDF_DATA_DIR / <filename>)
      2. pdfscurso directory  (PDFSCURSO_DIR / <relative-path>)
      3. Absolute path as-is (legacy paths from the v2 container)
    """
    if not pdf_path:
        return None

    # 0. Handle API URL paths — /api/pdf/<id> (v3) and legacy
    #    /api/pdf/serve/<filename> (v2).
    if pdf_path.startswith("/api/pdf/"):
        rest = pdf_path.split("/api/pdf/")[-1].split("?")[0]
        # v3 pattern: /api/pdf/<numeric-id> → look up storage_path in pdfs.
        if rest.isdigit():
            from database import query_one  # local import keeps this module pure-ish

            row = query_one(
                "SELECT storage_path FROM pdfs WHERE id = %s", (int(rest),)
            )
            if row and row.get("storage_path") and os.path.isfile(row["storage_path"]):
                return row["storage_path"]
            return None
        # v2 pattern: /api/pdf/serve/<filename>.pdf → uploads folder.
        uploaded = os.path.join(PDF_DATA_DIR, rest)
        if os.path.isfile(uploaded):
            return uploaded
        return None

    # 1. Uploaded PDFs
    uploaded = os.path.join(PDF_DATA_DIR, pdf_path)
    if os.path.isfile(uploaded):
        return uploaded
    # 2. pdfscurso
    pdfscurso = os.path.join(PDFSCURSO_DIR, pdf_path)
    if os.path.isfile(pdfscurso):
        return pdfscurso
    # 3. Absolute path as-is (last resort)
    if pdf_path.startswith("/") and os.path.isfile(pdf_path):
        return pdf_path
    return None