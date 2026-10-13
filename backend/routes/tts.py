# backend/routes/tts.py
"""TTS generation route for StudyFlow PWA v3."""

import tempfile
import os
from flask import Blueprint, request, jsonify, send_file

from routes.auth import token_required
from tts_engine import TTSError, synthesize


bp = Blueprint('tts', __name__)


@bp.post('/tts/generate')
@token_required
def generate_tts(current_user_id: int):
    """Generate TTS audio using edge-tts."""
    data = request.get_json() or {}

    text = data.get('text', '').strip()
    voice = data.get('voice', 'es-ES-ElviraNeural')
    rate = data.get('rate', '+0%')
    volume = data.get('volume', '+0%')

    if not text:
        return jsonify({'error': 'text is required'}), 400

    # Create temp file for output
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
        output_path = tmp.name

    result_path = output_path
    try:
        # Synthesize with edge-tts, falling back to macOS `say` on failure
        # (backend/tts_engine.py). The fallback returns an .m4a/AAC file.
        result = synthesize(text, voice, output_path, rate=rate, volume=volume)
        result_path = result.path

        return send_file(
            result.path,
            mimetype=result.mimetype,
            as_attachment=True,
            download_name=os.path.basename(result.path),
        )

    except TTSError as e:
        return jsonify({'error': 'TTS generation failed', 'details': str(e)}), 500
    except Exception as e:  # noqa: BLE001 — keep the route from 500-ing uncaught
        return jsonify({'error': 'TTS generation failed', 'details': str(e)}), 500
    finally:
        # Clean up temp file(s) (in production, you'd want to stream it)
        for path in {output_path, result_path}:
            try:
                os.unlink(path)
            except OSError:
                pass