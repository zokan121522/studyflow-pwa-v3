# backend/routes/pdf.py
"""PDF upload/serve routes for StudyFlow PWA v3."""

import os
import uuid
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