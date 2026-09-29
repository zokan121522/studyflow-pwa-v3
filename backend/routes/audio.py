# backend/routes/audio.py
"""Audio/TTS routes for StudyFlow PWA v3."""

from flask import Blueprint, request, jsonify

from database import execute, fetchone, fetchall
from routes.auth import token_required
from models import AudioFile


bp = Blueprint('audio', __name__)


@bp.get('/audio/text-to-speech')
@token_required
def get_tts_audio(current_user_id: int):
    """Get TTS audio for a text (generate or retrieve cached)."""
    text = request.args.get('text', '').strip()
    voice = request.args.get('voice', 'es-ES-ElviraNeural')
    language = request.args.get('language', 'es')

    if not text:
        return jsonify({'error': 'text parameter is required'}), 400

    # Check cache
    row = fetchone(
        '''SELECT * FROM audio_files 
           WHERE user_id = %s AND text_content = %s AND voice = %s AND language = %s
           ORDER BY created_at DESC LIMIT 1''',
        (current_user_id, text, voice, language)
    )

    if row:
        audio = AudioFile.from_row(row)
        if audio.audio_url:
            return jsonify({'audio_url': audio.audio_url, 'cached': True})

    # In a real implementation, this would call edge-tts or another TTS service
    # For now, return a placeholder
    audio_url = f'/api/audio/generate-placeholder?text={text}&voice={voice}'

    # Save to cache
    execute(
        '''INSERT INTO audio_files (user_id, title, text_content, audio_url, voice, language)
           VALUES (%s, %s, %s, %s, %s, %s)''',
        (current_user_id, text[:50], text, audio_url, voice, language)
    )

    return jsonify({'audio_url': audio_url, 'cached': False})


@bp.post('/audio/tts')
@token_required
def generate_tts(current_user_id: int):
    """Generate TTS audio and save it."""
    data = request.get_json() or {}

    text = data.get('text', '').strip()
    voice = data.get('voice', 'es-ES-ElviraNeural')
    language = data.get('language', 'es')
    title = data.get('title', text[:50])

    if not text:
        return jsonify({'error': 'text is required'}), 400

    # In a real implementation, this would call edge-tts
    # For now, return a placeholder
    audio_url = f'/api/audio/generate-placeholder?text={text}&voice={voice}'

    execute(
        '''INSERT INTO audio_files (user_id, title, text_content, audio_url, voice, language)
           VALUES (%s, %s, %s, %s, %s, %s)''',
        (current_user_id, title, text, audio_url, voice, language)
    )

    row = fetchone(
        'SELECT * FROM audio_files WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    return jsonify({'audio': AudioFile.from_row(row).to_dict()}), 201


@bp.get('/audio')
@token_required
def list_audio(current_user_id: int):
    """List all audio files for the current user."""
    rows = fetchall(
        'SELECT * FROM audio_files WHERE user_id = %s ORDER BY created_at DESC',
        (current_user_id,)
    )
    return jsonify({'audio': [AudioFile.from_row(r).to_dict() for r in rows]})


@bp.delete('/audio/<int:audio_id>')
@token_required
def delete_audio(current_user_id: int, audio_id: int):
    """Delete an audio file."""
    result = execute('DELETE FROM audio_files WHERE id = %s AND user_id = %s', (audio_id, current_user_id))
    if result == 0:
        return jsonify({'error': 'Audio not found'}), 404

    return jsonify({'message': 'Audio deleted successfully'})