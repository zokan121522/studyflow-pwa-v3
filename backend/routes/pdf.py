# backend/routes/pdf.py
"""PDF upload/serve/annotate routes for StudyFlow PWA v3 (S7)."""

import os
import uuid
import json
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename

from backend.database import execute, fetchone, fetchall
from backend.routes.auth import token_required
from backend.models import PDF


bp = Blueprint('pdf', __name__)

# Configuration
UPLOAD_FOLDER = os.environ.get('PDF_UPLOAD_FOLDER', '/app/uploads/pdfs')
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 50 * 1024 * 1024))  # 50MB
ALLOWED_EXTENSIONS = {'pdf'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _own_pdf(pdf_id: int, user_id: int):
    """Return the row for a PDF that belongs to the user, or None."""
    return fetchone(
        'SELECT * FROM pdfs WHERE id = %s AND user_id = %s',
        (pdf_id, user_id)
    )


@bp.post('/pdf/upload')
@token_required
def upload_pdf(current_user_id: int):
    """Upload a PDF file."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files are allowed'}), 400

    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({'error': f'File too large. Max size: {MAX_FILE_SIZE} bytes'}), 413

    # Generate unique filename
    original_name = secure_filename(file.filename)
    ext = original_name.rsplit('.', 1)[1].lower()
    filename = f'{uuid.uuid4().hex}.{ext}'
    storage_path = os.path.join(UPLOAD_FOLDER, filename)

    # Save file
    file.save(storage_path)

    # Get page count (optional - would need PyMuPDF)
    page_count = None
    try:
        import fitz
        doc = fitz.open(storage_path)
        page_count = doc.page_count
        doc.close()
    except Exception:
        pass

    # Get metadata
    course_id = request.form.get('course_id', type=int)
    topic_id = request.form.get('topic_id', type=int)

    execute(
        '''INSERT INTO pdfs (user_id, course_id, topic_id, filename, original_name, file_size, page_count, storage_path)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
        (current_user_id, course_id, topic_id, filename, original_name, file_size, page_count, storage_path)
    )

    row = fetchone(
        'SELECT * FROM pdfs WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    return jsonify({'pdf': PDF.from_row(row).to_dict()}), 201


@bp.get('/pdf/<int:pdf_id>')
@token_required
def get_pdf(current_user_id: int, pdf_id: int):
    """Serve a PDF file."""
    row = fetchone('SELECT * FROM pdfs WHERE id = %s AND user_id = %s', (pdf_id, current_user_id))
    if not row:
        return jsonify({'error': 'PDF not found'}), 404

    pdf = PDF.from_row(row)
    if not os.path.exists(pdf.storage_path):
        return jsonify({'error': 'File not found on disk'}), 404

    return send_file(
        pdf.storage_path,
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
        os.remove(row['storage_path'])
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
        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
    }