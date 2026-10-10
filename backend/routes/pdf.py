# backend/routes/pdf.py
"""PDF upload/serve/annotate routes for StudyFlow PWA v3 (S7 + S7b).

S7b — unified PDF + SCORM import endpoint:
  POST /pdf/import     → multipart file OR JSON {mode:'scorm',…}
                        ?stream=1 on the SCORM branch → text/event-stream
                        progress frames (see backend/scorm_stream.py)
  GET  /pdf/import/status → {pdf, scorm_zip, moodle_scraping} capability flags
"""

import os
import uuid
import json
import logging
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from serial import iso

from database import execute, fetchone, fetchall
from routes.auth import token_required
from storage_paths import category_dir, resolve_media
from models import PDF


bp = Blueprint('pdf', __name__)
logger = logging.getLogger(__name__)

# Configuration
# storage_paths is the single resolver: PDF_UPLOAD_FOLDER when the deploy
# sets it (Docker), otherwise the repo-local <root>/uploads/pdfs this module
# used to compute for itself. The export, the restore and the AI readers all
# ask the same function, which is what makes a restored PDF land where this
# route serves it -- on every OS, not just in the container.
UPLOAD_FOLDER = category_dir('pdfs')
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 50 * 1024 * 1024))  # 50MB
ALLOWED_EXTENSIONS = {'pdf'}

# Create the folder, but never let an unwritable path kill the whole app at
# import time — uploads then fail with a clear 5xx instead of a dead server.
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except OSError as exc:  # pragma: no cover — depends on host permissions
    logger.warning(
        'PDF upload folder %s is not writable (%s); PDF uploads will fail',
        UPLOAD_FOLDER, exc,
    )


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _own_pdf(pdf_id: int, user_id: int):
    """Return the row for a PDF that belongs to the user, or None."""
    return fetchone(
        'SELECT * FROM pdfs WHERE id = %s AND user_id = %s',
        (pdf_id, user_id)
    )


def _store_uploaded_pdf(
    current_user_id: int,
    file_storage,
    *,
    course_id=None,
    topic_id=None,
) -> dict:
    """Reusable helper: validate + save a PDF FileStorage and return its dict.

    Raises ValueError for user-visible validation failures (caller turns
    into 400/413 JSON). Anything else propagates as 500 by the global
    error handler.
    """
    if not file_storage or not file_storage.filename:
        raise ValueError('No file provided')
    if not allowed_file(file_storage.filename):
        raise ValueError('Only PDF files are allowed')

    file_storage.seek(0, os.SEEK_END)
    file_size = file_storage.tell()
    file_storage.seek(0)
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f'File too large. Max size: {MAX_FILE_SIZE} bytes')
    if file_size == 0:
        raise ValueError('Empty file')

    original_name = secure_filename(file_storage.filename) or 'upload.pdf'
    ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else 'pdf'
    storage_filename = f"{uuid.uuid4().hex}.{ext}"
    storage_path = os.path.join(UPLOAD_FOLDER, storage_filename)
    file_storage.save(storage_path)

    page_count = None
    try:
        import fitz
        doc = fitz.open(storage_path)
        page_count = doc.page_count
        doc.close()
    except Exception:
        pass

    execute(
        '''INSERT INTO pdfs (user_id, course_id, topic_id, filename, original_name, file_size, page_count, storage_path)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
        (current_user_id, course_id, topic_id, storage_filename,
         original_name, file_size, page_count, storage_path),
    )
    row = fetchone(
        'SELECT * FROM pdfs WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,),
    )
    if not row:
        raise RuntimeError('PDF row not found after insert')
    return PDF.from_row(row).to_dict()


@bp.post('/pdf/upload')
@token_required
def upload_pdf(current_user_id: int):
    """Upload a PDF file (legacy endpoint, kept for backwards compat).

    New code should call POST /pdf/import instead — same response shape
    plus a uniform {ok, mode, ...} envelope via the JSON branch.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    course_id = request.form.get('course_id', type=int)
    topic_id = request.form.get('topic_id', type=int)
    try:
        pdf_dict = _store_uploaded_pdf(
            current_user_id, file,
            course_id=course_id, topic_id=topic_id,
        )
    except ValueError as err:
        return jsonify({'error': str(err)}), 400
    return jsonify({'pdf': pdf_dict}), 201


@bp.get('/pdf/<int:pdf_id>')
@token_required
def get_pdf(current_user_id: int, pdf_id: int):
    """Serve a PDF file."""
    row = fetchone('SELECT * FROM pdfs WHERE id = %s AND user_id = %s', (pdf_id, current_user_id))
    if not row:
        return jsonify({'error': 'PDF not found'}), 404

    pdf = PDF.from_row(row)
    pdf_path = resolve_media('pdfs', pdf.storage_path)
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'File not found on disk'}), 404

    return send_file(
        pdf_path,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=pdf.original_name
    )


@bp.delete('/pdf/<int:pdf_id>')
@token_required
def delete_pdf(current_user_id: int, pdf_id: int):
    """Delete a PDF."""
    row = fetchone('SELECT * FROM pdfs WHERE id = %s AND user_id = %s', (pdf_id, current_user_id))
    if not row:
        return jsonify({'error': 'PDF not found'}), 404

    # Delete file from disk
    try:
        os.remove(resolve_media('pdfs', row['storage_path']))
    except OSError:
        pass  # File already gone

    execute('DELETE FROM pdfs WHERE id = %s', (pdf_id,))
    return jsonify({'message': 'PDF deleted successfully'})


@bp.get('/pdfs')
@token_required
def list_pdfs(current_user_id: int):
    """List all PDFs for the current user."""
    course_id = request.args.get('course_id', type=int)
    topic_id = request.args.get('topic_id', type=int)

    query = 'SELECT * FROM pdfs WHERE user_id = %s'
    params = [current_user_id]

    if course_id:
        query += ' AND course_id = %s'
        params.append(course_id)
    if topic_id:
        query += ' AND topic_id = %s'
        params.append(topic_id)

    query += ' ORDER BY created_at DESC'

    rows = fetchall(query, tuple(params))
    return jsonify({'pdfs': [PDF.from_row(r).to_dict() for r in rows]})


# ─── S7: annotations ────────────────────────────────────────────────
# Persist lightweight per-page annotations (text notes, free-form JSON
# blobs the viewer chooses to round-trip). The viewer is responsible for
# any geometry; we just store what it sends keyed by (pdf_id, page).


@bp.post('/pdf/annotate')
@token_required
def annotate_pdf(current_user_id: int):
    """Save (upsert) an annotation for (pdf_id, page).

    Body JSON: { pdf_id: int, page: int (>=1), data: {...} }
    Returns:   { annotation: { id, pdf_id, page, data, created_at, updated_at } }
    """
    payload = request.get_json(silent=True) or {}
    pdf_id = payload.get('pdf_id')
    page = payload.get('page')
    data = payload.get('data')
    if not isinstance(pdf_id, int) or pdf_id <= 0:
        return jsonify({'error': 'pdf_id must be a positive int'}), 400
    if not isinstance(page, int) or page < 1:
        return jsonify({'error': 'page must be a positive int'}), 400
    if not isinstance(data, (dict, list)):
        return jsonify({'error': 'data must be a JSON object or array'}), 400

    if not _own_pdf(pdf_id, current_user_id):
        return jsonify({'error': 'PDF not found'}), 404

    data_json = json.dumps(data)
    # Upsert: one annotation row per (pdf_id, page); the viewer replaces
    # the whole payload each time (no fine-grained merge).
    execute(
        """
        INSERT INTO pdf_annotations (user_id, pdf_id, page, data, updated_at)
        VALUES (%s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (pdf_id, page) DO UPDATE
          SET data = EXCLUDED.data, updated_at = NOW()
        """,
        (current_user_id, pdf_id, page, data_json),
    )
    row = fetchone(
        'SELECT * FROM pdf_annotations '
        'WHERE pdf_id = %s AND page = %s AND user_id = %s',
        (pdf_id, page, current_user_id),
    )
    return jsonify({'annotation': _annotation_to_dict(row)}), 201


@bp.get('/pdf/<int:pdf_id>/annotations')
@token_required
def list_pdf_annotations(current_user_id: int, pdf_id: int):
    """List every annotation row for a PDF (one per page)."""
    if not _own_pdf(pdf_id, current_user_id):
        return jsonify({'error': 'PDF not found'}), 404
    rows = fetchall(
        'SELECT * FROM pdf_annotations '
        'WHERE pdf_id = %s AND user_id = %s ORDER BY page ASC',
        (pdf_id, current_user_id),
    )
    return jsonify({'annotations': [_annotation_to_dict(r) for r in rows]})


def _annotation_to_dict(row) -> dict:
    """Convert a pdf_annotations row → JSON-safe dict.

    The `data` JSONB column comes back as already-parsed Python objects
    via RealDictCursor — only re-parse when psycopg2 returned a str.
    """
    data = row['data']
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {'raw': data}
    return {
        'id': row['id'],
        'pdf_id': row['pdf_id'],
        'page': row['page'],
        'data': data,
        'created_at': iso(row['created_at']),
        'updated_at': iso(row['updated_at']),
    }


# ─── S7b: Unified PDF + SCORM import ───────────────────────────────
# Frontend posts ONE form to /pdf/import:
#   • multipart with 'file' field          → upload a local PDF
#   • JSON   {mode:'scorm', url|path, …}   → import a SCORM ZIP OR scrape
# Single button (App.PdfImport.open) dispatches based on the chosen tab.
# All branches return the SAME envelope so the popover can render uniformly.
# Capability flags live at GET /pdf/import/status so the UI hides tabs
# whose backend branches are unavailable.


def _import_status() -> dict:
    """Capability snapshot for the unified import popover."""
    # Local import is always available (just the upload helper + stdlib zip)
    from scorm_import import (
        is_scorm_zip_available, is_moodle_scraping_available,
    )
    return {
        'pdf': True,
        'scorm_zip': is_scorm_zip_available(),
        'moodle_scraping': is_moodle_scraping_available(),
    }


@bp.get('/pdf/import/status')
@token_required
def import_status(current_user_id: int):
    """Return capability flags for the unified import popover."""
    return jsonify(_import_status()), 200


@bp.post('/pdf/import')
@token_required
def import_pdf_or_scorm(current_user_id: int):
    """Single unified import endpoint — see routes/pdf.py module docstring.

    • multipart with `file` field     → upload a local PDF
    • JSON {mode:'scorm', path|url,…} → SCORM zip or Moodle scrape
                                         (+ ?stream=1 → SSE progress)
    • otherwise                       → 400 {error}
    """
    if request.files:
        return _import_via_upload(current_user_id)
    payload = request.get_json(silent=True) or {}
    if (payload.get('mode') or '').strip().lower() != 'scorm':
        return jsonify({
            'ok': False,
            'reason': "Body must be multipart 'file' or JSON {mode:'scorm',…}",
        }), 400
    if request.args.get('stream') == '1':
        return _stream_scorm_import(current_user_id, payload)
    return _import_via_scorm(current_user_id, payload)


def _import_via_upload(current_user_id: int):
    """Handle multipart 'file' branch → upload + register PDF."""
    file = request.files.get('file')
    course_id = request.form.get('course_id', type=int)
    topic_id = request.form.get('topic_id', type=int)
    try:
        pdf_dict = _store_uploaded_pdf(
            current_user_id, file,
            course_id=course_id, topic_id=topic_id,
        )
    except ValueError as err:
        return jsonify({'ok': False, 'reason': str(err)}), 400
    return jsonify({
        'ok': True,
        'mode': 'pdf-file',
        'pdf': pdf_dict,
        'url': f"/api/pdf/{pdf_dict['id']}",
        'title': pdf_dict['original_name'],
    }), 201


def _import_via_scorm(current_user_id: int, payload: dict):
    """Handle JSON SCORM branch → zip import or Moodle scrape."""
    from scorm_import import import_scorm
    result = import_scorm(
        current_user_id,
        url=(payload.get('url') or '').strip(),
        path=(payload.get('path') or '').strip(),
        course_id=payload.get('course_id'),
        topic_id=payload.get('topic_id'),
        title=(payload.get('title') or '').strip(),
    )
    # Per S7b spec: SCORM soft-errors stay 200 (envelope has {ok:false}).
    # Only success-with-blocks bumps to 201 so the popover can distinguish.
    status = 201 if (result.get('ok') and result.get('count', 0) > 0) else 200
    return jsonify(result), status


def _stream_scorm_import(current_user_id: int, payload: dict):
    """SSE variant of the SCORM branch: step frames, then one final frame.

    The import runs on a worker thread because the Selenium scrape can
    outlive any proxy timeout (Cloudflare → HTTP 524 after 100s silence).
    """
    from flask import current_app
    from scorm_import import import_scorm
    from scorm_stream import stream_import

    app = current_app._get_current_object()  # real app, not a LocalProxy
    args = {
        'url': (payload.get('url') or '').strip(),
        'path': (payload.get('path') or '').strip(),
        'course_id': payload.get('course_id'),
        'topic_id': payload.get('topic_id'),
        'title': (payload.get('title') or '').strip(),
    }

    def work(emit):
        # _save_pdf_and_block reads current_app.config → needs app context.
        with app.app_context():
            return import_scorm(current_user_id, progress_cb=emit, **args)

    return stream_import(work)