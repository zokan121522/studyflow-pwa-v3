# backend/routes/tts.py
"""TTS generation route for StudyFlow PWA v3."""

import subprocess
import tempfile
import os
from flask import Blueprint, request, jsonify, send_file

from backend.routes.auth import token_required


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

    try:
        # Call edge-tts
        cmd = [
            'edge-tts',
            '--text', text,
            '--voice', voice,
            '--rate', rate,
            '--volume', volume,
            '--write-media', output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return jsonify({'error': 'TTS generation failed', 'details': result.stderr}), 500

        # Return the audio file
        return send_file(
            output_path,
            mimetype='audio/mpeg',
            as_attachment=True,
            download_name='tts_output.mp3'
        )

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'TTS generation timed out'}), 504
    except FileNotFoundError:
        return jsonify({'error': 'edge-tts not installed'}), 500
    except Exception as e:
        return jsonify({'error': 'TTS generation failed', 'details': str(e)}), 500
    finally:
        # Clean up temp file (in production, you'd want to stream it)
        try:
            os.unlink(output_path)
        except OSError:
            pass