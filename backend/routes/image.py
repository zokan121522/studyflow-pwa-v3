"""Image blocks (R4): upload, serve and delete images from a mounted volume.

Deliberately mirrors `routes/pdf.py` — same shape, same error handling, one
less pattern to learn. It deviates from it in exactly two places, and both
exist because images are served back to the browser:

* **The bytes decide the type, not the filename.** `pdf.py` trusts
  `secure_filename`'s extension. For an image that is a stored-XSS
  primitive the moment something sniffs the response: a `.png` whose
  payload is HTML gets rendered as a document. So `_sniff_image` reads the
  magic bytes and the stored extension is derived from *that*. The
  uploaded extension never reaches the filesystem.
* **`X-Content-Type-Options: nosniff`** on every served image, so the
  declared `image/*` cannot be second-guessed by the browser.

Ownership is enforced like everywhere else. Note that `token_required`
falls back to `LOCAL_USER_ID` when no `Authorization` header is present,
which is what lets a plain `<img src="...">` work at all — the PWA has no
cookie session, it passes the JWT by header from `fetch`.
"""
from __future__ import annotations

import os
import uuid

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from database import execute, execute_returning, fetchone
from models import Image
from routes.auth import token_required
from storage_paths import category_dir, resolve_media

bp = Blueprint('image', __name__)

# One resolver for every media folder (storage_paths): IMAGE_UPLOAD_FOLDER
# when the deploy sets it, else backend/uploads/images -- exactly what this
# module resolved on its own before, now shared with the backup export and
# restore so an image round-trips between them untouched.
UPLOAD_FOLDER = category_dir('images')

# 15 MB. Images in a study block are diagrams and screenshots; anything
# larger is a file that belongs in the docs tab, not inline.
MAX_FILE_SIZE = int(os.environ.get('MAX_IMAGE_SIZE', 15 * 1024 * 1024))

# (magic bytes, extension, mime). Only real raster formats: no SVG, which
# is a script container and would defeat the nosniff header above.
_SNIFF_TABLE = (
    (b'\x89PNG\r\n\x1a\n', 'png', 'image/png'),
    (b'\xff\xd8\xff', 'jpg', 'image/jpeg'),
)

_SNIFF_LEN = 16


def _sniff_image(head: bytes):
    """Return `(ext, mime)` for a supported image, or None.

    `head` is the first `_SNIFF_LEN` bytes of the upload.
    """
    for magic, ext, mime in _SNIFF_TABLE:
        if head.startswith(magic):
            return ext, mime
    return None


def _title_from_filename(filename: str) -> str:
    """Derive the block title from the uploaded filename, minus extension.

    The spec for R4 is "the name it was uploaded under becomes the title", so
    this stays verbatim apart from the extension and collapsed whitespace — no
    prettifying, no case changes, no surprise.

    Deliberately fed the RAW filename, not `original_name`. secure_filename()
    rewrites spaces to underscores, so deriving the title from the sanitised
    name turns "Diagrama flujo.png" into "Diagrama_flujo" — the sanitised name
    exists to build paths, and a title is not a path.
    """
    base = os.path.basename(str(filename or ''))
    stem = base.rsplit('.', 1)[0] if '.' in base else base
    return ' '.join(stem.split()) or 'Imagen'


def _own_image(image_id: int, user_id: int):
    return fetchone(
        'SELECT * FROM images WHERE id = %s AND user_id = %s',
        (image_id, user_id),
    )


def _own_course(course_id: int, user_id: int) -> bool:
    return bool(fetchone(
        'SELECT id FROM courses WHERE id = %s AND user_id = %s',
        (course_id, user_id),
    ))


def _store_uploaded_image(
    current_user_id: int,
    file_storage,
    *,
    course_id=None,
    topic_id=None,
) -> dict:
    """Validate, save and register an uploaded image. Returns its dict.

    Raises ValueError for user-visible validation failures (the caller turns
    those into 400/413 JSON); anything else surfaces as a 500.
    """
    if not file_storage or not file_storage.filename:
        raise ValueError('No file provided')

    file_storage.seek(0, os.SEEK_END)
    file_size = file_storage.tell()
    file_storage.seek(0)
    if file_size == 0:
        raise ValueError('Empty file')
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f'File too large. Max size: {MAX_FILE_SIZE} bytes')

    head = file_storage.read(_SNIFF_LEN)
    file_storage.seek(0)
    sniffed = _sniff_image(head)
    if not sniffed:
        raise ValueError('Only PNG and JPEG images are supported')
    ext, mime = sniffed

    # The client's name is kept for display only. `secure_filename` strips
    # path separators and control characters, so it is safe as a title and
    # never becomes a path.
    raw_name = file_storage.filename or 'imagen'
    title = _title_from_filename(raw_name)
    original_name = secure_filename(raw_name) or 'imagen'

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    storage_filename = f'{uuid.uuid4().hex}.{ext}'
    storage_path = os.path.join(UPLOAD_FOLDER, storage_filename)
    file_storage.save(storage_path)

    # RETURNING * identifies the row this INSERT produced. The previous
    # `ORDER BY id DESC LIMIT 1` assumed ids are chronological, which a
    # restored database breaks: a stale row with a higher id was returned
    # instead, and the editor embedded a file_url that 404'd.
    row = execute_returning(
        '''INSERT INTO images
             (user_id, course_id, topic_id, filename, original_name, mime,
              file_size, storage_path)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *''',
        (current_user_id, course_id, topic_id, storage_filename,
         original_name, mime, file_size, storage_path),
    )
    if not row:
        raise RuntimeError('Image row not found after insert')
    stored = Image.from_row(row).to_dict()
    # Not a column: the block owns the title, and this is what the front-end
    # hands straight to the create call.
    stored['title'] = title
    return stored


def delete_image_file(current_user_id: int, image_id: int) -> bool:
    """Delete an image row and its file. Returns False if it was not found.

    Shared by the DELETE endpoint and by `routes.blocks._reap_image`, so an
    image block and its image can never drift apart no matter which one is
    removed first.
    """
    row = _own_image(image_id, current_user_id)
    if not row:
        return False

    storage_path = resolve_media('images', row['storage_path'])
    execute('DELETE FROM images WHERE id = %s AND user_id = %s', (image_id, current_user_id))

    # The row is gone; a leftover file on the volume is only litter.
    if storage_path:
        try:
            os.remove(storage_path)
        except OSError:
            current_app.logger.warning('Could not remove image file %s', storage_path)
    return True


# ============================== Routes ==============================
@bp.post('/image/upload')
@token_required
def upload_image(current_user_id: int):
    """Upload an image. Returns metadata; the front-end then creates the block."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    course_id = request.form.get('course_id', type=int)
    topic_id = request.form.get('topic_id', type=int)
    if course_id and not _own_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404

    try:
        image = _store_uploaded_image(
            current_user_id,
            request.files['file'],
            course_id=course_id,
            topic_id=topic_id,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    image['file_url'] = f"/api/image/{image['id']}/file"
    return jsonify({'image': image}), 201


@bp.get('/image/<int:image_id>')
@token_required
def get_image(current_user_id: int, image_id: int):
    """Image metadata, for the edit panel."""
    row = _own_image(image_id, current_user_id)
    if not row:
        return jsonify({'error': 'Image not found'}), 404
    image = Image.from_row(row).to_dict()
    image['file_url'] = f'/api/image/{image_id}/file'
    return jsonify({'image': image})


@bp.get('/image/<int:image_id>/file')
@token_required
def get_image_file(current_user_id: int, image_id: int):
    """Serve the bytes. Reached by a bare `<img src>`, hence the headers."""
    row = _own_image(image_id, current_user_id)
    if not row:
        return jsonify({'error': 'Image not found'}), 404

    storage_path = resolve_media('images', row['storage_path'])
    if not storage_path or not os.path.exists(storage_path):
        return jsonify({'error': 'Image file missing on disk'}), 404

    resp = send_file(
        storage_path,
        mimetype=row['mime'],
        conditional=True,
    )
    # No Content-Disposition: nothing about the user's filename belongs in
    # a header the browser will parse. Immutable content-addressed name, so
    # a day of caching is safe and keeps the block re-render cheap.
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@bp.delete('/image/<int:image_id>')
@token_required
def delete_image(current_user_id: int, image_id: int):
    """Delete an image. Blocks keep existing; their image_id is nulled."""
    if not delete_image_file(current_user_id, image_id):
        return jsonify({'error': 'Image not found'}), 404
    return jsonify({'deleted': True, 'id': image_id})