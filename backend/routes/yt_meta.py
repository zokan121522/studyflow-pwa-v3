"""
YouTube video metadata API — lightweight endpoint for the markdown embed feature.

  GET /api/yt/meta?url=https://youtu.be/...  → {videoId, title, duration, thumbnail}

Design (issue #216, Opción A): reuse the same yt-dlp machinery as youtube_zen
(_ytdlp_base_cmd + _run_ytdlp_with_retry) — no YouTube Data API key needed.
Results are cached in memory for 24h per videoId so repeated renders (and the
floating PiP re-embeds) don't hammer YouTube.

Duration is used by the frontend card ("06:42") but is never required: if the
fetch fails (private video, network, rate-limit) the endpoint still returns
200 with an empty title and null duration, and the frontend falls back to the
static thumbnail + link text.
"""

import re
import time
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from routes.auth import token_required

bp = Blueprint("yt_meta", __name__)

# ── In-memory cache ─────────────────────────────────────────────────
_CACHE_TTL_SECONDS = 24 * 3600  # 24h

_meta_cache: dict[str, dict] = {}

# Allowed YouTube hosts (audit run-1, yt-meta-ytdlp-ssrf): the raw attacker
# URL is never forwarded to yt-dlp — only a canonical watch URL is built
# from the extracted video id and passed down.
_ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def _validate_youtube_url(url: str) -> bool:
    """Return True only for https URLs on a genuine YouTube host."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in _ALLOWED_YOUTUBE_HOSTS
    )


def _extract_video_id(url: str) -> str:
    """Extract the 11-char YouTube video ID from a URL.

    Supports: watch?v=, youtu.be/, /embed/, /shorts/, live.
    """
    if not url:
        return ""
    match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/(?:embed|shorts|live)/([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)
    return ""


def _canonical_watch_url(video_id: str) -> str:
    """Build the canonical YouTube watch URL used as the yt-dlp target."""
    return f"https://www.youtube.com/watch?v={video_id}"


def _fetch_meta(video_id: str) -> dict | None:
    """Fetch title/duration/thumbnail via yt-dlp (best-effort, may be slow).

    Reuses the shared cookies + JS-solver flags and the retry/backoff policy
    from youtube_zen, then falls back to no-cookies on failure.
    Only the canonical watch URL is passed to yt-dlp — never a user-supplied
    location (SSRF containment).
    """
    from ai.notebooklm.youtube_zen import (
        _run_ytdlp_with_retry,
        _ytdlp_base_cmd,
    )

    target_url = _canonical_watch_url(video_id)

    for with_cookies in (True, False):
        try:
            result = _run_ytdlp_with_retry(
                _ytdlp_base_cmd(with_cookies=with_cookies)
                + [
                    "--print", "%(title)s\t%(duration)s\t%(thumbnail)s",
                    "--no-playlist", "--skip-download", target_url,
                ],
                timeout=30,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue
            title, duration, thumbnail = result.stdout.strip().split("\t", 2)
            return {
                "videoId": video_id,
                "title": title.strip(),
                "duration": _parse_duration_secs(duration.strip()),
                "thumbnail": thumbnail.strip() or
                    f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            }
        except Exception:
            continue
    return None


def _parse_duration_secs(raw: str) -> int | None:
    """Parse yt-dlp %(duration)s (seconds or 'NA') into int seconds."""
    try:
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


@bp.get("/yt/meta")
@token_required
def get_meta(current_user_id: int):
    """Return metadata for a YouTube URL, with a 24h in-memory cache."""
    url = (request.args.get("url") or "").strip()
    # SSRF containment (audit run-1): only real YouTube https URLs are accepted.
    if not _validate_youtube_url(url):
        return jsonify(error="URL de YouTube no válida"), 400
    video_id = _extract_video_id(url)
    if not video_id:
        return jsonify(error="URL de YouTube no válida"), 400

    cached = _meta_cache.get(video_id)
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return jsonify(cached["data"])

    data = _fetch_meta(video_id)
    if data is None:
        data = {
            "videoId": video_id,
            "title": "",
            "duration": None,
            "thumbnail": f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
        }
    _meta_cache[video_id] = {"ts": time.time(), "data": data}
    return jsonify(data)
