"""
YouTube → OpenZEN: Local subtitle extraction + AI structuring.

Uses yt-dlp to extract subtitles/transcripts locally, then sends
the cleaned text to OpenZEN (big-pickle) for structuring into
clean markdown. No NotebookLM API dependency, no rate limits.

Exports:
    create_youtube_zen_task
    resume_youtube_zen_task
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time

import database as db
from ai.providers import get_provider
from ai.generation.prompts import MD_TEMPLATES, get_md_template_role, _length_instruction
from ai.notebooklm.utils import _new_task_id, _coerce_id

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Language instructions — prepended FIRST so the model registers the
# output language rule before the task (pattern from knowledge_pipeline).
# ═══════════════════════════════════════════════════════════════════
_LANG_INSTRUCTIONS = {
    "es": "¡IMPORTANTE! Debes responder EXCLUSIVAMENTE en español. "
          "No uses inglés bajo ningún concepto. Todo el contenido debe estar en español.",
    "en": "IMPORTANT! You MUST respond EXCLUSIVELY in English. "
          "Never use Spanish or any other language. All content must be in English.",
}

# ═══════════════════════════════════════════════════════════════════
# Faithfulness rule — balanced fidelity with bounded pedagogical inference.
# Preprended to ALL prompts.
#
# Distinguishes two kinds of inference:
#   ✅ ALLOWED  — make explicit the pedagogical method / learning structure
#                 the video DEMONSTRATES through its examples and scenes
#                 (e.g. "this video uses scene X to teach vocabulary of Y").
#   ❌ FORBIDDEN — invent facts, figures, statistics, names, examples or
#                 concepts NOT supported NOR demonstrated by the video.
#
# Rationale (Phase 52): many educational videos are *demonstrative* — the
# lesson lives in WHAT they show, not what they verbalize. A rule that
# forbids ALL inference makes the model pad with generic content it can
# already justify, missing the essence entirely. The inference must remain
# anchored to evidence in the video (a scene, an example, a demonstrated
# structure) — never free invention.
# ═══════════════════════════════════════════════════════════════════
_FAITHFULNESS_RULE = (
    "CRITICAL RULE — FAITHFULNESS (balanced):\n"
    "Your content MUST be grounded in the video represented by the transcript. "
    "You are ALLOWED to infer and make explicit the pedagogical method or "
    "learning structure that the video DEMONSTRATES through its examples and "
    "scenes, even when it is not verbatim in the spoken words (e.g., infer "
    "'this video uses the X scene to teach vocabulary about Y'). This inferred "
    "method must always be anchored to evidence in the video — a scene, an "
    "example, or a structure it demonstrates.\n"
    "You MUST NOT invent facts, figures, statistics, names, examples, or "
    "concepts that are neither stated NOR demonstrated by the video. If the "
    "video omits something, do not fabricate it. Do not pad sections with "
    "generic filler; the depth must match the video's real content.\n"
)

# ═══════════════════════════════════════════════════════════════════
# Depth-aware system prompts (adapted from knowledge_pipeline) — each
# one keeps the faithfulness rule embedded via the caller prefix.
# ═══════════════════════════════════════════════════════════════════
_PROMPT_CONCISE = (
    "You are an expert educational content creator. "
    "Generate a CONCISE but COMPLETE summary of the transcript that captures "
    "the video's essence (150-300 words, or proportional to the transcript — "
    "do NOT pad a short transcript to hit a fixed word count).\n\n"
    "Structure:\n"
    "- **Opening sentence**: What the video covers, in one clear line\n"
    "- **Core concepts**: The 3-5 most important ideas FROM the transcript "
    "(for a short/demonstrative transcript, these may include the pedagogical "
    "method the video demonstrates), each explained in 1-2 sentences\n"
    "- **Key distinction**: What makes this different from similar concepts "
    "(only if the transcript supports it)\n"
    "- **Why it matters**: One sentence on practical relevance (from the transcript)\n"
    "- **Quick reference**: A mini-table or bullet list of the essentials\n\n"
    "Rules:\n"
    "- Use precise, academic language — no filler, no fluff\n"
    "- Bold key terms on first use\n"
    "- Every sentence must teach something concrete\n"
    "- If the transcript is short, do NOT invent content to reach the target "
    "length — synthesize the demonstrated method and stop\n"
    "- Reply ONLY with the markdown content\n"
    "- NO single H1 wrapping the whole note, NO introductions, NO farewells\n"
)

_PROMPT_STANDARD = (
    "You are an expert educational content creator. "
    "Generate a STANDARD educational note that teaches the transcript "
    "content clearly and thoroughly. Target 500-1000 words ONLY IF the "
    "transcript supports that depth; for shorter/demonstrative transcripts, "
    "scale the depth to the content — do NOT pad with generic filler to "
    "reach a fixed length.\n\n"
    "Required sections (adapt each to the transcript — if the transcript "
    "lacks material for a section, briefly synthesize it from the method the "
    "video demonstrates, or omit it rather than inventing):\n"
    "1. **Definition** — What the video's topic is, in 3-4 sentences with precise terminology "
    "(for demonstrative videos, define BOTH the surface topic AND the method it demonstrates)\n"
    "2. **Key Concepts** — Break down into the core components present in the transcript "
    "(including the demonstrated pedagogical method where relevant), each explained with concrete references to the video\n"
    "3. **How It Works** — Step-by-step or flow explanation present in the transcript\n"
    "4. **Practical Examples** — The examples the transcript/ video actually shows, "
    "with increasing complexity (do NOT invent examples; use what the video demonstrates)\n"
    "5. **Common Pitfalls** — 3-4 mistakes or caveats the transcript/ video mentions "
    "(ONLY include pitfalls the video actually demonstrates — never invent them)\n"
    "6. **Key Takeaways** — Bullet list of the most important points the video actually conveys\n\n"
    "Rules:\n"
    "- Use the rich Obsidian format specified at the end: emoji ## section headings, colored callouts, **bold** for key terms\n"
    "- Use tables for comparisons when there are 3+ items to compare (from the transcript)\n"
    "- Every concept MUST be traceable to the transcript OR to the method the video demonstrates\n"
    "- NEVER invent pitfalls, examples, statistics, or concepts the video does not state nor show\n"
    "- Reply ONLY with the markdown content\n"
    "- NO title headers (##), NO introductions, NO farewells\n"
)

# ─── Detailed Mode: Plan + Dynamic Chunks ────────────────────────

_PROMPT_PLAN = (
    "You are a senior university professor organizing a comprehensive course note "
    "from a video transcript.\n\n"
    "Given the transcript below, generate a DETAILED OUTLINE for a thorough course note.\n"
    "The outline must have 8-15 sections (### headings), organized logically.\n\n"
    "IMPORTANT: Only create sections for content that EXISTS in the transcript. "
    "Reply with ONLY the numbered outline. Each line must be:\n"
    "N. **Section Title** — brief description (target N words) @ MM:SS\n\n"
    "The optional '@ MM:SS' suffix is the APPROXIMATE minute/seconds where the "
    "section STARTS in the video (e.g. @ 12:34). Use it whenever you can spot "
    "the topic transition in the transcript. If unsure, you may omit it.\n\n"
    "Example format:\n"
    "1. **Definition & Context** — Precise definition from the transcript (400 words) @ 00:30\n"
    "2. **Core Principles** — 5 fundamental principles mentioned (500 words) @ 08:15\n"
    "3. **Architecture** — Internal mechanisms explained in the video (400 words) @ 21:40\n"
    "... and so on\n\n"
    "Guidelines for the outline:\n"
    "- Start with Definition/Overview\n"
    "- Include Principles/Fundamentals early\n"
    "- Architecture/Mechanism in the middle\n"
    "- Examples/Practice after theory\n"
    "- End with Takeaways\n"
    "- Each section target: 300-500 words (total will be 3000-6000 words)\n"
    "- Aim for 10-12 sections for a complex topic, 8-9 for simpler ones\n"
    "- Do NOT invent sections the transcript does not support\n\n"
    "Reply with ONLY the numbered outline. No other text.\n"
)

_PROMPT_CHUNK_TEMPLATE = (
    "You are a senior university professor creating comprehensive, authoritative course material "
    "from a video transcript.\n"
    "Generate sections {start}-{end} of a DETAILED course note.\n"
    "This is chunk {chunk_num} of {total_chunks}.\n\n"
    "The full outline is:\n"
    "{outline}\n\n"
    "Generate ONLY sections {start}-{end} listed above.\n\n"
    "Requirements for EACH section:\n"
    "- Depth is proportional to the transcript's real content: for a rich "
    "transcript aim for 300-500 words; for a sparse/demonstrative transcript "
    "scale down and synthesize the method the video demonstrates. NEVER pad "
    "with generic filler to hit a target word count.\n"
    "- Use the rich Obsidian format specified at the end (emoji ## section headings, colored callouts, **bold** key terms)\n"
    "- Include code blocks with language tags and inline comments (only if the transcript mentions code)\n"
    "- Use tables for comparisons (minimum 3 items) when the transcript compares things\n"
    "- Use blockquotes > for important notes, warnings, tips the transcript/ video demonstrates\n"
    "- Every claim MUST be traceable to the transcript OR to the method the video demonstrates\n\n"
    "Rules:\n"
    "- Generate ONLY the sections specified — do NOT skip ahead or add extra sections\n"
    "- Do NOT repeat content from other chunks\n"
    "- NEVER repeat the same word or phrase multiple times in a row — if you run out of things to say, STOP\n"
    "- NEVER invent facts, figures, statistics, names, examples or concepts the video neither states nor demonstrates\n"
    "- Reply ONLY with the markdown content\n"
    "- NO single H1 wrapping the whole note, NO introductions, NO farewells\n"
    "- NO conversational meta-text: never add confirmations, status updates, "
    "questions ('Do you want me to continue?', '¿Quieres que continúe?'), "
    "summaries of what you did, 'Section N is complete', or any text outside "
    "the section content itself. Output ONLY the sections' markdown.\n"
)

# ═══════════════════════════════════════════════════════════════════
# Rich Obsidian output format — appended LAST to every generation prompt
# so it is the most recent formatting instruction (overrides heading
# rules inside the base prompts). Produces colored callouts, bold key
# terms and emoji section headings, matching the course block style.
# ═══════════════════════════════════════════════════════════════════
_FORMAT_RICH = (
    "OUTPUT FORMAT — RICH OBSIDIAN MARKDOWN (MANDATORY):\n"
    "- Render each required section as an emoji H2 heading, e.g. "
    "`## 📖 Definición`, `## 🔗 Key Concepts`, `## ⚠️ Pitfalls`, "
    "`## 🚀 Takeaways` (section names in the OUTPUT language).\n"
    "- Put a colored Obsidian callout right after each heading:\n"
    "  - `> [!info]` — blue facts\n"
    "  - `> [!warning]` — red/orange pitfalls or mistakes\n"
    "  - `> [!tip]` — green advice\n"
    "  - `> [!example]` — purple examples\n"
    "  Each callout: 2-4 sentences, first line a short bold label.\n"
    "  If the transcript lacks material for multiple callouts, use ONE "
    "informative callout per section rather than fabricating content.\n"
    "- Bold (`**term**`) every important term on FIRST use.\n"
    "- Use markdown tables for comparisons of 3+ items ONLY when the "
    "transcript/ video actually compares things (do NOT force a table that "
    "invents comparison structure).\n"
    "- Use fenced code blocks (```lang) ONLY if the transcript mentions code.\n"
    "- NEVER wrap the whole note in a single H1 title; do NOT add "
    "introductions or farewells.\n"
    "- This format REPLACES the heading rules in the prompt above.\n"
)

# ═══════════════════════════════════════════════════════════════════
# Rich Obsidian output format for por_tema mode — same visual richness
# (colored callouts, bold terms, tables) but ONE heading per section of
# the plan/outline, so the frontend por_tema split creates one block per
# section instead of exploding into dozens of emoji sub-headings.
# ═══════════════════════════════════════════════════════════════════
_FORMAT_RICH_POR_TEMA = (
    "OUTPUT FORMAT — RICH OBSIDIAN MARKDOWN, ONE SECTION PER HEADING (MANDATORY):\n"
    "- Generate exactly ONE heading per section listed in the outline/plan "
    "above. Use ONLY plain `## Section Title` headings (level 2). NEVER use "
    "`###` or deeper headings — each section must have exactly ONE `##` "
    "heading. Do NOT split each section into many emoji sub-headings like "
    "`## 📖 Definición`.\n"
    "- Inside EACH section, use colored Obsidian callouts for the key "
    "content:\n"
    "  - `> [!info]` — blue facts\n"
    "  - `> [!warning]` — red/orange pitfalls or mistakes\n"
    "  - `> [!tip]` — green advice\n"
    "  - `> [!example]` — purple examples\n"
    "  Each callout: 2-4 sentences, first line a short bold label.\n"
    "  If the transcript lacks material, use fewer/short callouts rather "
    "than fabricating content.\n"
    "- Bold (`**term**`) every important term on FIRST use.\n"
    "- Use markdown tables for comparisons of 3+ items ONLY when the "
    "transcript/ video actually compares things (do NOT force a table that "
    "invents comparison structure).\n"
    "- Use fenced code blocks (```lang) ONLY if the content mentions code.\n"
    "- NEVER wrap the whole note in a single H1 title; do NOT add "
    "introductions or farewells.\n"
    "- CRITICAL: keep ONE section per heading — group related concepts "
    "under the section heading with callouts instead of creating new "
    "headings. NEVER use `###` sub-headings; use callouts or bold labels "
    "for sub-topics inside a section.\n"
    "- NEVER append conversational meta-text after a section (no 'Do you "
    "want me to continue?', no 'Section complete', no status updates). "
    "The section markdown ends at the LAST content line; nothing else.\n"
    "- This format REPLACES the heading rules in the prompt above.\n"
)

# ═══════════════════════════════════════════════════════════════════
# Timestamp link instruction — appended when mode is "por_tema" and
# timestamp data is available.  Tells the AI to place a clickable
# YouTube link at the end of each section pointing to the exact
# minute in the video where that content starts.
# ═══════════════════════════════════════════════════════════════════
_TIMESTAMP_INSTRUCTION = (
    "YOUTUBE TIMESTAMP LINKS (MANDATORY — use the timestamps provided in the user message):\n"
    "- At the END of each section, add a clickable YouTube link that jumps to "
    "the exact moment in the video where that section's content starts.\n"
    "- Format: `[▶ MM:SS](https://www.youtube.com/watch?v=VIDEO_ID?t=SECONDS)`\n"
    "- Replace VIDEO_ID with the actual video ID from the URL in the user message.\n"
    "- For EACH section you generate, pick the CLOSEST timestamp from the list "
    "provided in the user message that corresponds to when that content was discussed.\n"
    "- Replace SECONDS with the integer timestamp closest to that section's content.\n"
    "- Replace MM:SS with the human-readable version of that timestamp.\n"
    "- Place the link on its OWN line right after the section's last callout/content, "
    "before the next heading.\n"
    "- Example: `[▶ 05:30](https://www.youtube.com/watch?v=ABgLEKFhlZE?t=330)`\n"
    "- You MUST add a timestamp link to EVERY section. Do NOT skip any section.\n"
)

# ── Steps for progress reporting ────────────────────────────────────
_STEPS_YT_ZEN = [
    ("validate",  "🔗 Validando URL de YouTube..."),
    ("extract",   "📥 Extrayendo subtítulos localmente (yt-dlp)..."),
    ("clean",     "🧹 Limpiando transcript..."),
    ("opencode",  "🤖 Estructurando con OpenZEN..."),
    ("save",      "💾 Guardando resultado..."),
]

_CHUNK_MAX_CHARS = 8000

# Min real content chars (after stripping AI meta-text) for a chunk to be
# accepted. A chunk that only contains conversational chatter is INVALID
# and must raise so the caller persists recoverable state for retry.
_MIN_CHUNK_CONTENT_CHARS = 300

# Max sections per chunk (1 per chunk — smaller output per call, fewer empty responses)
_CHUNK_SIZE = 1

# Max retry attempts for a failed detailed chunk before forcing the user
# to either skip it or cancel (avoids infinite retry loops). The limit is
# PER CHUNK — with many chunks (15+) and transient provider failures a low
# number exhausts quickly, so it is configurable via YTZEN_MAX_CHUNK_RETRIES
# and defaults to 5. On resume the *current* value always wins (see
# resume_youtube_zen_task) so already-failed tasks benefit too.
_MAX_CHUNK_RETRIES = int(os.environ.get("YTZEN_MAX_CHUNK_RETRIES", "5"))

# Max idle time between stream deltas before the chunk is aborted as a
# recoverable failure (Phase 59, issue #228). A stalled OpenZEN stream
# would otherwise leave the task 'processing' forever and block the FIFO
# queue. The check runs between deltas; a companion read-timeout in the
# SSE client (opencode_provider) enforces the same bound when NO deltas
# arrive at all, so a dead stream always surfaces as a retryable chunk
# error instead of a zombie task.
_STREAM_IDLE_TIMEOUT_SECONDS = int(os.environ.get("YTZEN_STREAM_IDLE_TIMEOUT", "300"))

# ── yt-dlp rate-limit retry policy ──────────────────────────────────
# YouTube throttles the server IP with HTTP 429 (Too Many Requests) or
# transient 5xx errors. These clear up on their own, so we retry with a
# growing backoff before declaring failure (instead of failing the whole
# task on the first 429).
_YTDLP_RETRY_ATTEMPTS = 3
_YTDLP_RETRY_BACKOFF_SECONDS = (5, 15, 30)
# Per-attempt subprocess timeout. YouTube can intermittently throttle / stall
# requests (seen as "Command ... timed out after 120 seconds"); catching the
# timeout as a retryable attempt gives a thawed request a second chance.
_YTDLP_TIMEOUT_SECONDS = 180

# ── yt-dlp cookies + JS solver ─────────────────────────────────────
# YouTube flags the server IP as a bot ("Sign in to confirm you're not
# a bot", HTTP 429) and gate some formats behind a JS challenge. Fixes:
#   1. Cookies exported from a real Chrome session (Netscape format)
#      prove the request comes from a logged-in browser.
#   2. The Deno runtime + the EJS remote JS component let yt-dlp solve
#      YouTube's "n" challenge (deno is installed in the image; see
#      Dockerfile).
# The cookies file lives on the persistent /data volume so it survives
# container rebuilds (re-export it periodically — cookies expire).
_YTDLP_COOKIES_PATH = os.environ.get(
    "YTDLP_COOKIES_PATH", "/data/cookies/youtube_cookies.txt"
)
_YTDLP_REMOTE_COMPONENTS = "ejs:github"


class ChunkFailure(Exception):
    """Raised when a detailed-mode chunk returns an empty response.

    Carries the intermediate state (healthy parts + plan + sections) so the
    task can be resumed via retry-chunk / continue-without instead of losing
    all completed chunks to a fatal error.
    """

    def __init__(self, chunk_idx: int, num_chunks: int, parts: list[str],
                 plan_text: str, sections: list[dict], message: str):
        super().__init__(message)
        self.chunk_idx = chunk_idx
        self.num_chunks = num_chunks
        self.parts = parts
        self.plan_text = plan_text
        self.sections = sections
        self.message = message


# ── VTT cleaning ────────────────────────────────────────────────────

def _clean_vtt(vtt_text: str) -> str:
    """Clean WebVTT subtitle text: remove timestamps, deduplicate lines.

    Args:
        vtt_text: Raw VTT content.

    Returns:
        Cleaned plain text.
    """
    lines = vtt_text.split("\n")
    cleaned: list[str] = []
    seen: set[str] = set()

    for line in lines:
        line = line.strip()
        # Skip VTT headers, timestamps, empty lines
        if not line:
            continue
        if line.startswith("WEBVTT"):
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"^\d{2}:\d{2}[:.]", line):
            continue
        if re.match(r"^\d+$", line):
            continue
        # Remove VTT tags like <c> </c> etc
        clean = re.sub(r"<[^>]+>", "", line)
        clean = clean.strip()
        if not clean:
            continue
        # Deduplicate consecutive identical lines (common in auto-subs)
        if clean not in seen:
            seen.add(clean)
            cleaned.append(clean)

    return "\n".join(cleaned)


# ── VTT timestamp extraction ──────────────────────────────────────

def _dedupe_rollup_lines(
    timestamps: list[tuple[int, str]],
    window_seconds: int = 15,
) -> list[tuple[int, str]]:
    """Drop ONLY the lines duplicated by YouTube's roll-up format.

    YouTube auto-sub VTT uses a roll-up layout: every subtitle block
    repeats the lines already shown in the previous blocks.  The same
    phrase therefore appears once per following block, inflating the
    transcript (and the generation prompt) with duplicated content.

    This drops a line only when it repeats a previously-seen line that
    falls within ``window_seconds``.  A phrase that legitimately
    reappears at a different moment of the video (outside the window)
    is preserved, keeping the transcript faithful.

    Args:
        timestamps: ``(timestamp_seconds, cleaned_line)`` pairs as
            produced by :func:`_extract_timestamps_from_vtt`.
        window_seconds: Max gap (in seconds) between repeats that is
            considered roll-up duplication.  Defaults to 15 (roll-up
            blocks span ~2-4 s, so 15 s safely covers them).

    Returns:
        A new list with roll-up repeats removed.  The first occurrence
        of each line (with its timestamp) is kept.
    """
    deduped: list[tuple[int, str]] = []
    last_seen: dict[str, int] = {}
    for ts, line in timestamps:
        prev_ts = last_seen.get(line)
        # Compare against the LAST occurrence seen (kept or dropped) so a
        # long continuous roll-up is fully covered: as long as repeats keep
        # arriving within the window, they keep being dropped.
        is_rollup_repeat = prev_ts is not None and ts - prev_ts <= window_seconds
        last_seen[line] = ts
        if not is_rollup_repeat:
            deduped.append((ts, line))
    return deduped


def _extract_timestamps_from_vtt(vtt_text: str) -> list[tuple[int, str]]:
    """Extract (seconds, text) pairs from raw WebVTT.

    Parses timestamp lines like ``00:05:30.000 --> 00:05:35.000`` and
    associates each with the text that follows until the next timestamp.

    Args:
        vtt_text: Raw VTT content (before cleaning).

    Returns:
        List of ``(timestamp_seconds, cleaned_line)`` tuples.  Each
        subtitle line appears once with the timestamp of its block.
        Roll-up duplicate lines (YouTube auto-subs repeat previous
        lines in every block) are removed by
        :func:`_dedupe_rollup_lines`, keeping legitimate repeats that
        happen at different moments of the video.
    """
    timestamps: list[tuple[int, str]] = []
    current_seconds = 0

    for line in vtt_text.split("\n"):
        line = line.strip()
        # Parse timestamp: "00:05:30.000 --> 00:05:35.000"
        ts_match = re.match(r"(\d{2}):(\d{2}):(\d{2})\.\d+\s*-->", line)
        if ts_match:
            h, m, s = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3))
            current_seconds = h * 3600 + m * 60 + s
            continue
        # Skip VTT headers, sequence numbers, empty lines
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"^\d+$", line):
            continue
        # Clean VTT tags
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean:
            timestamps.append((current_seconds, clean))

    return _dedupe_rollup_lines(timestamps)


def _map_chunk_timestamps(
    chunks: list[str],
    vtt_timestamps: list[tuple[int, str]],
) -> list[dict]:
    """Map each cleaned chunk to its approximate timestamp range.

    Uses the first and last meaningful lines of each chunk to find the
    corresponding timestamps in the VTT data.

    Args:
        chunks: List of cleaned text chunks.
        vtt_timestamps: Output of :func:`_extract_timestamps_from_vtt`.

    Returns:
        List of dicts with keys ``start`` (int seconds), ``end`` (int
        seconds), and ``start_fmt`` (``"MM:SS"`` string).
    """
    if not vtt_timestamps:
        return []

    chunk_ts: list[dict] = []
    search_start = 0  # optimization: don't re-scan from the beginning

    for chunk in chunks:
        # First meaningful line (~100 chars) as search anchor
        first_line = chunk[:200].split("\n")[0].strip()
        # Last meaningful line as end anchor
        last_line = chunk[-200:].split("\n")[-1].strip()

        start_sec = 0
        end_sec = 0

        # Find start: scan from last known position
        anchor = first_line[:40]
        for i in range(search_start, len(vtt_timestamps)):
            _, text = vtt_timestamps[i]
            if anchor and (anchor in text or text[:40] in anchor):
                start_sec = vtt_timestamps[i][0]
                search_start = i
                break

        # Find end: scan from start position forward
        end_anchor = last_line[:40]
        for i in range(search_start, len(vtt_timestamps)):
            _, text = vtt_timestamps[i]
            if end_anchor and (end_anchor in text or text[:40] in end_anchor):
                end_sec = vtt_timestamps[i][0]

        # Fallback: if end not found, estimate 120s per chunk
        if end_sec <= start_sec:
            end_sec = start_sec + 120

        minutes = start_sec // 60
        seconds = start_sec % 60
        # Collect ALL unique timestamps within [start_sec, end_sec],
        # sampled every ~90s to keep the list manageable (max ~8 per chunk).
        timestamps_in_range: list[dict] = []
        last_added = -999
        for ts_sec, _text in vtt_timestamps:
            if start_sec <= ts_sec <= end_sec and (ts_sec - last_added) >= 90:
                tm, ts = divmod(ts_sec, 60)
                timestamps_in_range.append({"sec": ts_sec, "fmt": f"{tm:02d}:{ts:02d}"})
                last_added = ts_sec

        # Always include the end timestamp
        if end_sec > start_sec and (not timestamps_in_range or timestamps_in_range[-1]["sec"] != end_sec):
            tm, ts = divmod(end_sec, 60)
            timestamps_in_range.append({"sec": end_sec, "fmt": f"{tm:02d}:{ts:02d}"})

        chunk_ts.append({
            "start": start_sec,
            "end": end_sec,
            "start_fmt": f"{minutes:02d}:{seconds:02d}",
            "timestamps": timestamps_in_range,
        })

    return chunk_ts


def _build_timestamp_block(
    video_url: str,
    vtt_timestamps: list[tuple[int, str]],
    max_items: int = 12,
) -> str:
    """Build a TIMESTAMP DATA block from raw VTT timestamps, sampled.

    Used in por_tema + detailed mode where the plan-based chunking doesn't
    align with VTT ranges: we sample the whole list every ~90s (bounded by
    ``max_items``) and let the AI pick the CLOSEST one per section.

    Args:
        video_url: Original YouTube URL (video id is extracted for links).
        vtt_timestamps: List of (seconds, text) from the raw VTT.
        max_items: Cap on how many sampled timestamps to include.

    Returns:
        A formatted block of timestamp lines, or "" when no data.
    """
    if not vtt_timestamps:
        return ""
    video_id = _extract_video_id(video_url)
    if not video_id:
        return ""

    # Sample every ~90s, capped at max_items, always include first and last
    sampled: list[dict] = []
    step = max(90, (vtt_timestamps[-1][0] - vtt_timestamps[0][0]) // max(max_items, 1))
    last_added = -999
    for ts_sec, _text in vtt_timestamps:
        if len(sampled) >= max_items:
            break
        if (ts_sec - last_added) >= step:
            tm, ts = divmod(ts_sec, 60)
            sampled.append({"sec": ts_sec, "fmt": f"{tm:02d}:{ts:02d}"})
            last_added = ts_sec
    # Ensure the final timestamp is included
    last_sec = vtt_timestamps[-1][0]
    if not sampled or sampled[-1]["sec"] != last_sec:
        tm, ts = divmod(last_sec, 60)
        sampled.append({"sec": last_sec, "fmt": f"{tm:02d}:{ts:02d}"})

    ts_lines = [f"- Video ID: {video_id}"]
    for ts_item in sampled:
        ts_lines.append(
            f"- {ts_item['fmt']} ({ts_item['sec']}s) → "
            f"https://www.youtube.com/watch?v={video_id}?t={ts_item['sec']}"
        )
    return "\n\nTIMESTAMP DATA — pick the CLOSEST timestamp for each section:\n" + "\n".join(ts_lines) + "\n"


def _chunk_text(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """Split text into chunks ≤ max_chars on paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        sep = para + "\n\n"
        if current_len + len(sep) > max_chars and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(sep)

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text]


# ── Shared helpers (ported from knowledge_pipeline) ─────────────────

def _sanitize_output(text: str) -> str:
    """Truncate repetition loops (same word 5+ times or same line 3+ times)."""
    if not text:
        return text

    # Pattern 1: same word repeated 5+ times (e.g. "a-rooted a-rooted ...")
    repeat_word = re.compile(
        r'((?:\S+\s+){0,3}\S+)(\s+\1){4,}', re.IGNORECASE,
    )
    match = repeat_word.search(text)
    if match:
        text = text[:match.start()].rstrip()

    # Pattern 2: same line repeated 3+ times
    lines = text.split("\n")
    sanitized: list[str] = []
    prev_line = ""
    repeat_count = 0
    for line in lines:
        stripped = line.strip()
        if stripped and stripped == prev_line:
            repeat_count += 1
            if repeat_count >= 3:
                # Truncate at this point
                break
        else:
            repeat_count = 0
            prev_line = stripped
        sanitized.append(line)

    return "\n".join(sanitized).rstrip()


# ═══════════════════════════════════════════════════════════════════
# AI meta-text patterns — conversational chatter the model sometimes
# appends AFTER the real content (e.g. on chunk retry/resume the model
# may answer "Section N is complete... Do you want me to continue?").
# This chatter MUST NOT reach the final blocks.
# ═══════════════════════════════════════════════════════════════════
_META_TEXT_PATTERNS = (
    re.compile(r"¿Quieres que continúe", re.IGNORECASE),
    re.compile(r"¿Continúo con", re.IGNORECASE),
    re.compile(r"Do you want me to", re.IGNORECASE),
    re.compile(r"Want me to generate", re.IGNORECASE),
    re.compile(r"Should I proceed", re.IGNORECASE),
    re.compile(r"To continue, I'?d need", re.IGNORECASE),
    re.compile(r"The original task explicitly said", re.IGNORECASE),
    re.compile(r"The task specified only sections", re.IGNORECASE),
    re.compile(r"so I've stopped", re.IGNORECASE),
    re.compile(r"I've completed section", re.IGNORECASE),
    re.compile(r"completed section \d+", re.IGNORECASE),
    re.compile(r"No tengo más pasos pendientes", re.IGNORECASE),
    re.compile(r"encargo original", re.IGNORECASE),
    re.compile(r"detengo aquí", re.IGNORECASE),
    re.compile(r"ya está generada y completa", re.IGNORECASE),
    re.compile(r"está completa y cumple todos los requisitos", re.IGNORECASE),
    re.compile(r"\bsection\s+\d+[^\n]{0,40}\bis complete\b", re.IGNORECASE),
    re.compile(r"\bla sección\s+\d+[^\n]{0,40}\bcompleta\b", re.IGNORECASE),
    re.compile(r"remaining chunks \(", re.IGNORECASE),
)


def _strip_ai_meta_text(text: str) -> str:
    """Remove AI conversational meta-text appended after real content.

    On chunk retry/resume the model sometimes answers with confirmation
    chatter ("Section N is complete... Do you want me to continue?")
    instead of only the section content. That chatter must never reach
    the stored blocks. We locate the FIRST meta pattern and truncate at
    the last horizontal rule before it (or at its start if none), so the
    real content (and its timestamp link) is preserved.
    """
    if not text:
        return text

    cut = None
    for pat in _META_TEXT_PATTERNS:
        m = pat.search(text)
        if m:
            cut = m.start() if cut is None else min(cut, m.start())
    if cut is None:
        return text

    prefix = text[:cut]
    hr = prefix.rfind("\n---\n")
    if hr != -1:
        return prefix[:hr].rstrip()
    return prefix.rstrip()


def _check_cancelled(task_id: str) -> bool:
    """Check if user cancelled the task."""
    row = db.query_one(
        "SELECT status FROM ai_tasks WHERE id = %s",
        (task_id,),
    )
    return row is not None and row["status"] == "cancelled"


def _parse_ts_marker(marker: str | None) -> int | None:
    """Parse an ``@ MM:SS`` (or ``@ H:MM:SS``) marker into seconds.

    Returns ``None`` when the marker is missing or unparseable, so sections
    without a timestamp keep working (the pipeline falls back to the full
    transcript for that chunk).
    """
    if not marker:
        return None
    parts = marker.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return None


def _parse_outline(outline_text: str) -> list[dict]:
    """Parse the plan output into a list of sections.

    Returns list of {"num": int, "title": str, "desc": str, "words": int,
    "start_sec": int | None}. ``start_sec`` comes from an optional trailing
    ``@ MM:SS`` marker (approximate video time where the section starts).
    """
    sections = []
    # Match lines like: "1. **Title** — description (N words) @ 12:34"
    pattern = re.compile(
        r"(\d+)\.\s*\*\*(.+?)\*\*\s*[—–-]\s*(.+?)(?:\s*\((\d+)\s*words?\))?"
        r"(?:\s*@\s*(\d{1,3}:\d{2}(?::\d{2})?))?\s*$",
        re.MULTILINE,
    )
    for match in pattern.finditer(outline_text):
        num = int(match.group(1))
        title = match.group(2).strip()
        desc = match.group(3).strip()
        words = int(match.group(4)) if match.group(4) else 400
        start_sec = _parse_ts_marker(match.group(5))
        sections.append({"num": num, "title": title, "desc": desc, "words": words, "start_sec": start_sec})

    # Fallback: if regex didn't match, try simpler "N. Title" pattern
    if not sections:
        simple_pattern = re.compile(r"(\d+)\.\s*\*\*(.+?)\*\*", re.MULTILINE)
        for match in simple_pattern.finditer(outline_text):
            num = int(match.group(1))
            title = match.group(2).strip()
            sections.append({"num": num, "title": title, "desc": "", "words": 400, "start_sec": None})

    return sections


def _build_dynamic_steps(plan_text: str, sections: list | None = None) -> list[tuple[str, str]]:
    """Build progress steps dynamically based on the plan.

    Uses the already-parsed sections when provided (what the generation
    flow actually uses, including the fallback), so step labels match the
    real chunk count and never show empty ranges like "secciones 1-0".
    """
    if sections is None:
        sections = _parse_outline(plan_text)
    num_chunks = max(1, (len(sections) + _CHUNK_SIZE - 1) // _CHUNK_SIZE)

    steps = [("validate", "📝 Validando entrada...")]
    steps.append(("plan", f"📋 Generando plan ({len(sections)} secciones estimadas)..."))
    for i in range(num_chunks):
        chunk_start = i * _CHUNK_SIZE + 1
        chunk_end = min((i + 1) * _CHUNK_SIZE, len(sections))
        chunk_end = max(chunk_start, chunk_end)
        steps.append((
            f"chunk_{i+1}",
            f"🤖 Generando chunk {i+1}/{num_chunks} (secciones {chunk_start}-{chunk_end})...",
        ))
    steps.append(("merge", "🔗 Combinando resultados..."))
    return steps


# ── yt-dlp subtitle extraction ─────────────────────────────────────

def _ytdlp_base_cmd(with_cookies: bool = True) -> list[str]:
    """Base yt-dlp invocation with the shared cookies + JS-solver flags.

    Returns e.g. ``["yt-dlp", "--cookies", "<path>",
    "--remote-components", "ejs:github"]``. Cookies are only added when
    the file actually exists AND with_cookies=True, so local/dev machines
    without an exported cookie file still work.
    """
    cmd = ["yt-dlp"]
    if with_cookies and os.path.isfile(_YTDLP_COOKIES_PATH):
        cmd += ["--cookies", _YTDLP_COOKIES_PATH]
    cmd += ["--remote-components", _YTDLP_REMOTE_COMPONENTS]
    return cmd


def _is_rate_limited(stderr: str) -> bool:
    """True if yt-dlp stderr reports a transient YouTube rate-limit (429/5xx)."""
    return bool(
        re.search(
            r"HTTP Error 429|HTTP Error 5\d\d|Too Many Requests",
            stderr,
            re.IGNORECASE,
        )
    )


def _run_ytdlp_with_retry(
    cmd: list[str],
    timeout: int = _YTDLP_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Run a yt-dlp command, retrying on transient failures (rate-limit or
    timeout).

    YouTube throttles the server IP (HTTP 429 Too Many Requests) and can
    intermittently stall requests, causing the subprocess to exceed its
    timeout. Both are transient: instead of failing the task on the first
    failure, we retry with a growing backoff (5s → 15s → 30s). Non-retryable
    errors (a clean non-zero exit) return immediately.

    TimeoutExpired is caught and treated as a retryable attempt the same way
    as a 429/5xx, so a slow/throttled YouTube request degrades into one more
    attempt instead of killing the whole extraction task.

    Returns:
        The CompletedProcess of the last attempt, or a synthesized result
        (returncode != 0, stderr describing the timeout) once attempts are
        exhausted — never raises.
    """
    result = None
    for attempt in range(_YTDLP_RETRY_ATTEMPTS):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as te:
            _err = (te.stderr or "") if isinstance(te.stderr, str) else ""
            result = subprocess.CompletedProcess(
                cmd, returncode=-1,
                stdout=(te.stdout or "") if isinstance(te.stdout, str) else "",
                stderr=_err + f"\n[timeout after {timeout}s]",
            )
            if attempt < _YTDLP_RETRY_ATTEMPTS - 1:
                logger.warning(
                    "yt-dlp timed out after %ss (attempt %d/%d) — retrying in %ss",
                    timeout, attempt + 1, _YTDLP_RETRY_ATTEMPTS,
                    _YTDLP_RETRY_BACKOFF_SECONDS[attempt],
                )
                time.sleep(_YTDLP_RETRY_BACKOFF_SECONDS[attempt])
            continue

        if not _is_rate_limited(result.stderr):
            return result
        if attempt < _YTDLP_RETRY_ATTEMPTS - 1:
            logger.warning(
                "yt-dlp rate-limited (HTTP 429/5xx), attempt %d/%d — "
                "retrying in %ss",
                attempt + 1,
                _YTDLP_RETRY_ATTEMPTS,
                _YTDLP_RETRY_BACKOFF_SECONDS[attempt],
            )
            time.sleep(_YTDLP_RETRY_BACKOFF_SECONDS[attempt])
    return result


def _extract_subtitles(url: str, lang: str = "en") -> str:
    """Extract subtitles from a YouTube video using yt-dlp.

    Tries auto-generated subtitles first, then manual.
    Selects the subtitle file by language suffix (deterministic), falling
    back to the other available language if the requested one is missing.
    Falls back to transcript extraction if subtitles fail.

    If cookies are stale (YouTube says "page needs to be reloaded"),
    retries without cookies.

    Args:
        url: YouTube video URL.
        lang: Preferred subtitle language ('es' or 'en').

    Returns:
        Raw VTT/subtitle text.

    Raises:
        RuntimeError: If extraction fails.
    """
    # Priority order: requested language first, then the other one.
    other = "es" if lang == "en" else "en"
    sub_lang = f"{lang},{other}"

    def _try_extract(cmd_flags: list[str]) -> str | None:
        """Try extraction with given base flags. Returns VTT text or None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_tpl = os.path.join(tmpdir, "sub")

            # Try auto-generated subtitles first (most common)
            for sub_flag in ["--write-auto-sub", "--write-sub"]:
                cmd = cmd_flags + [
                    sub_flag,
                    "--no-playlist",   # when URL has &list=, only grab THIS video
                    "--sub-lang", sub_lang,
                    "--sub-format", "vtt",
                    "--skip-download",
                    "-o", out_tpl,
                    url,
                ]
                result = _run_ytdlp_with_retry(cmd, timeout=_YTDLP_TIMEOUT_SECONDS)

                # Find any .vtt files produced — pick by language suffix first
                vtt_files = [f for f in os.listdir(tmpdir) if f.endswith(".vtt")]
                if vtt_files:
                    wanted = next(
                        (f for f in vtt_files if f.endswith(f".{lang}.vtt")),
                        None,
                    )
                    vtt_path = os.path.join(tmpdir, wanted or vtt_files[0])
                    with open(vtt_path, "r", encoding="utf-8") as f:
                        return f.read()
        return None

    # Attempt 1: with cookies (may be stale)
    text = _try_extract(_ytdlp_base_cmd(with_cookies=True))
    if text:
        return text

    # Attempt 2: without cookies (fallback for stale/expired cookies)
    logger.info("yt-dlp subtitle extraction failed with cookies, retrying without")
    text = _try_extract(_ytdlp_base_cmd(with_cookies=False))
    if text:
        return text

    # Fallback: try getting subtitles via yt-dlp JSON
    cmd_info = _ytdlp_base_cmd(with_cookies=False) + ["--dump-json", "--no-playlist", "--skip-download", url]
    result_info = _run_ytdlp_with_retry(cmd_info, timeout=60)
    if result_info.returncode == 0:
        import json
        try:
            info = json.loads(result_info.stdout)
            title = info.get("title", "")
            desc = info.get("description", "")
            # Use description as fallback if no subtitles available
            if desc and len(desc) > 100:
                return f"# {title}\n\n{desc}"
        except json.JSONDecodeError:
            pass

    raise RuntimeError(
        "No se pudieron extraer subtítulos. "
        "El video puede no tener subtítulos disponibles."
    )


def _extract_video_id(url: str) -> str:
    """Extract the YouTube video ID from a URL.

    Supports formats:
        - https://www.youtube.com/watch?v=ABgLEKFhlZE
        - https://youtu.be/ABgLEKFhlZE
        - https://www.youtube.com/embed/ABgLEKFhlZE

    Returns:
        The 11-character video ID, or "" if not found.
    """
    # youtube.com/watch?v=ID
    match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)
    # youtu.be/ID
    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)
    # youtube.com/embed/ID
    match = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)
    return ""


def _get_video_title(url: str) -> str:
    """Fetch a YouTube video's real title via yt-dlp (best-effort).

    Used for the generated block's nav title and the link callout.
    Falls back to no-cookies if stale cookies cause failure.

    Args:
        url: YouTube video URL.

    Returns:
        The video title, or "" if it can't be determined.
    """
    for with_cookies in (True, False):
        try:
            result = _run_ytdlp_with_retry(
                _ytdlp_base_cmd(with_cookies=with_cookies)
                + ["--print", "%(title)s", "--no-playlist", "--skip-download", url],
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
    return ""


# ── AI structuring (same pattern as OpenZEN PDF) ────────────────────

def build_yt_prompt(
    template_id: str | None,
    depth: str = "standard",
    language: str = "es",
    mode: str = "unitema",
) -> str:
    """Compose the YouTube-Zen system prompt from a template role.

    When ``template_id`` is provided, the pure role/architecture of the
    template (from MD_TEMPLATES, see ``get_md_template_role``) replaces the
    generic concise/standard base prompt.  The YT pipeline invariants are
    ALWAYS preserved: language instruction first → faithfulness rule → role
    → YT-specific length hint → YT-specific rich format (bounded to one
    section per heading when mode='por_tema').

    Args:
        template_id: Template id from MD_TEMPLATES or None (generic prompt).
        depth: 'concise', 'standard' or 'detailed' (maps to length hint).
        language: Output language ('es' or 'en').
        mode: 'unitema' or 'por_tema' (selects the output format rule).

    Returns:
        The full system prompt string.
    """
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["es"])
    format_rule = _FORMAT_RICH_POR_TEMA if mode == "por_tema" else _FORMAT_RICH

    role = get_md_template_role(template_id)
    if role is None:
        # No template → generic YT base prompt (current behaviour).
        base_prompt = _PROMPT_CONCISE if depth == "concise" else _PROMPT_STANDARD
        length_hint = ""
    else:
        base_prompt = role
        # YT length hint maps the depth level (standard now gets an explicit
        # FULL DEVELOPMENT instruction — it must not come out escueto).
        length_hint = _length_instruction(depth)

    system_prompt = (
        f"{lang_instruction}\n\n"
        f"{_FAITHFULNESS_RULE}\n\n"
        f"{base_prompt}\n\n"
        f"{length_hint}\n"
        f"{format_rule}"
    )
    return system_prompt


def _structure_with_ai(
    text: str,
    video_url: str,
    depth: str = "standard",
    language: str = "es",
    mode: str = "unitema",
    timestamp: dict | None = None,
    template_id: str | None = None,
) -> str:
    """Send extracted text to OpenZEN for structuring into markdown.

    Args:
        text: Cleaned transcript text.
        video_url: Original YouTube URL (for context).
        depth: One of 'concise', 'standard', 'detailed'.
        language: Output language ('es' or 'en').
        mode: 'unitema' (rich emoji headings, single block) or 'por_tema'
            (one heading per section so frontend split stays bounded).
        timestamp: Optional timestamp dict with 'start', 'end', 'start_fmt'.
            When provided with mode='por_tema', a YouTube link is appended.
        template_id: Optional template id from MD_TEMPLATES. When provided,
            the template architecture replaces the generic base prompt.

    Returns:
        Structured markdown content.

    Raises:
        RuntimeError: If AI returns empty response.
    """
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["es"])

    if template_id:
        system_prompt = build_yt_prompt(template_id, depth=depth, language=language, mode=mode)
    else:
        if depth == "concise":
            base_prompt = _PROMPT_CONCISE
        else:
            base_prompt = _PROMPT_STANDARD

        # Language instruction FIRST, then faithfulness rule, then task prompt,
        # then the depth length hint, then the rich Obsidian output format
        # (most recent formatting rule). por_tema uses the bounded variant so
        # the frontend split yields one block per section, not dozens of emoji
        # sub-headings.
        length_hint = _length_instruction(depth)
        format_rule = _FORMAT_RICH_POR_TEMA if mode == "por_tema" else _FORMAT_RICH
        system_prompt = f"{lang_instruction}\n\n{_FAITHFULNESS_RULE}\n\n{base_prompt}\n\n{length_hint}{format_rule}"

    # Append timestamp instruction when timestamp data is available
    use_timestamp = mode == "por_tema" and timestamp is not None
    if use_timestamp:
        system_prompt += f"\n\n{_TIMESTAMP_INSTRUCTION}"

    # Build timestamp block for the user message
    ts_block = ""
    if use_timestamp:
        video_id = _extract_video_id(video_url)
        ts_list = timestamp.get("timestamps", [])
        if ts_list:
            ts_lines = [f"- Video ID: {video_id}"]
            for ts_item in ts_list:
                ts_lines.append(
                    f"- {ts_item['fmt']} ({ts_item['sec']}s) → "
                    f"https://www.youtube.com/watch?v={video_id}?t={ts_item['sec']}"
                )
            ts_block = "\n\nTIMESTAMP DATA — pick the CLOSEST timestamp for each section:\n" + "\n".join(ts_lines) + "\n"
        else:
            ts_block = (
                f"\n\nTIMESTAMP DATA for this section:\n"
                f"- Video ID: {video_id}\n"
                f"- Section starts at: {timestamp['start_fmt']} ({timestamp['start']} seconds)\n"
                f"- YouTube link: https://www.youtube.com/watch?v={video_id}?t={timestamp['start']}\n"
            )

    user_message = (
        f"Here is the transcript from a YouTube video: {video_url}\n\n"
        "Please structure it into clean, organized markdown notes "
        f"(depth: {depth}), based ONLY on the transcript below:\n\n"
        f"{text}"
        f"{ts_block}"
    )

    provider = get_provider("opencode-acp")
    result = provider.chat(messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ])

    content = result.get("content", "").strip()
    if not content:
        raise RuntimeError("OpenZEN devolvió una respuesta vacía")
    return _sanitize_output(content)


def _plan_detailed(
    text: str,
    video_url: str,
    task_id: str,
    language: str = "es",
    template_id: str | None = None,
) -> tuple[str, list[dict]]:
    """Detailed mode — plan pass: outline → parsed sections.

    Args:
        text: Cleaned transcript text.
        video_url: Original YouTube URL (for context).
        task_id: Task id for cancellation checks.
        language: Output language ('es' or 'en').
        template_id: Optional template id from MD_TEMPLATES. When provided,
            the template architecture is prepended to the plan prompt so
            the outline follows the chosen structure (chunks then fill the
            resulting sections, keeping the plan-driven chunking intact).

    Returns:
        (plan_text, sections) — the raw outline and parsed section list.
        The plan is reused by retry-chunk / continue-without to avoid
        regenerating the outline (saves tokens on resume).

    Raises:
        RuntimeError: If the plan call returns empty or the task is cancelled.
    """
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["es"])
    provider = get_provider("opencode-acp")

    role = get_md_template_role(template_id)
    if role:
        plan_system = (
            f"{lang_instruction}\n\n{_FAITHFULNESS_RULE}\n\n{role}\n\n{_PROMPT_PLAN}"
        )
    else:
        plan_system = f"{lang_instruction}\n\n{_FAITHFULNESS_RULE}\n\n{_PROMPT_PLAN}"
    plan_user = (
        f"Here is the transcript from a YouTube video: {video_url}\n\n"
        "Generate a detailed outline for a comprehensive course note based "
        "ONLY on the transcript below:\n\n"
        f"{text}"
    )
    plan_result = provider.chat(messages=[
        {"role": "system", "content": plan_system},
        {"role": "user", "content": plan_user},
    ])
    plan_text = plan_result.get("content", "").strip()
    if not plan_text:
        raise RuntimeError("OpenZEN devolvió un plan vacío")

    if _check_cancelled(task_id):
        raise RuntimeError("Cancelado por el usuario")

    sections = _parse_outline(plan_text)
    if not sections:
        logger.warning(
            "Plan sin secciones parseables (%d chars). Primeros 500: %r",
            len(plan_text), plan_text[:500],
        )
        sections = [{"num": 1, "title": "Full Topic", "desc": text, "words": 400, "start_sec": None}]
    return plan_text, sections


def _retry_requested(task_id: str) -> bool:
    """True when the user asked to retry the CURRENT chunk (retry-now).

    The frontend POSTs to retry-now which flips ``coverage_data.retry_requested``
    while the task is still 'processing'. The streaming loop polls this flag
    (cheap single-row read, same cadence as the flush throttle) and aborts the
    in-flight chunk so it lands in the recoverable chunk_error state — the
    user can restart a chunk at ANY time, not just after a real failure.
    """
    row = db.query_one(
        "SELECT coverage_data FROM ai_tasks WHERE id = %s", (task_id,)
    )
    if not row or not row["coverage_data"]:
        return False
    try:
        state = json.loads(row["coverage_data"])
    except (json.JSONDecodeError, TypeError):
        return False
    return state.get("retry_requested") is True


def _slice_by_timestamps(
    vtt_timestamps: list[tuple[int, str]],
    start_sec: int | None,
    end_sec: int | None,
    overlap_sec: int = 15,
) -> str:
    """Reconstruct the transcript text for a [start_sec, end_sec) window.

    Args:
        vtt_timestamps: Output of :func:`_extract_timestamps_from_vtt`.
        start_sec: Window start in seconds (None → beginning of the video).
        end_sec: Window end in seconds (None → end of the video).
        overlap_sec: Extra seconds added on BOTH sides so the model gets
            transition context between adjacent sections.

    Returns:
        Cleaned transcript text covering the window ("" when empty).
    """
    if not vtt_timestamps:
        return ""
    lo = (start_sec or 0) - overlap_sec
    hi = (end_sec if end_sec is not None else vtt_timestamps[-1][0]) + overlap_sec
    parts = [text for sec, text in vtt_timestamps if lo <= sec < hi]
    return "\n".join(parts).strip()


def _slice_by_length(
    text: str,
    chunk_idx: int,
    num_chunks: int,
    overlap_chars: int = 2000,
) -> str:
    """Reconstruct a transcript slice by an even length split.

    Splits ``text`` into ``num_chunks`` roughly equal parts and returns the
    ``chunk_idx``-th part, expanded with ``overlap_chars`` on both sides so
    the model gets transition context between adjacent sections. Both bounds
    are snapped to line boundaries so sentences are never cut mid-word.

    Args:
        text: Full cleaned transcript.
        chunk_idx: 0-based chunk index.
        num_chunks: Total number of chunks.
        overlap_chars: Extra characters added on both sides.

    Returns:
        The sliced transcript text (the full text when ``num_chunks <= 1``).
    """
    if num_chunks <= 1 or not text:
        return text
    total = len(text)
    part = total / num_chunks
    lo = max(0, int(chunk_idx * part) - overlap_chars)
    hi = min(total, int((chunk_idx + 1) * part) + overlap_chars)
    # Snap to line boundaries: start at the beginning of a line, end at the
    # end of a line (lines are joined with \n, one subtitle line each).
    if lo > 0:
        nl = text.find("\n", lo)
        if nl != -1:
            lo = nl + 1
    if hi < total:
        nl = text.rfind("\n", 0, hi)
        if nl != -1:
            hi = nl + 1
    return text[lo:hi].strip()


def _chunk_transcript_for(
    chunk_idx: int,
    num_chunks: int,
    sections: list[dict],
    vtt_timestamps: list | None,
    full_text: str = "",
) -> str | None:
    """Transcript slice for ONE chunk, or None when slicing is impossible.

    Two slicing strategies, in order of preference:

    1. **Plan markers** — when the plan carries ``@ MM:SS`` markers, the slice
       runs from the first section with a ``start_sec`` inside this chunk to
       the first marker of the NEXT chunk (or the video end for the last
       chunk). This aligns the transcript with the real section boundaries.
    2. **Even length split** (deterministic fallback) — when no markers are
       available (legacy plan, resumed task, or the model skipped the
       markers), the transcript is split into ``num_chunks`` roughly equal
       parts by length, so each chunk ALWAYS receives only its part instead of
       the full transcript.

    ``None`` is returned only when there is no way to slice (no timestamps AND
    no full text), in which case the caller passes the full transcript.

    Args:
        chunk_idx: 0-based chunk index.
        num_chunks: Total number of chunks.
        sections: Parsed plan sections (may carry ``start_sec``).
        vtt_timestamps: Output of :func:`_extract_timestamps_from_vtt`.
        full_text: Full cleaned transcript (for the length fallback).

    Returns:
        Sliced transcript text, or None to use the full transcript.
    """
    chunk_start = chunk_idx * _CHUNK_SIZE
    chunk_end = min(chunk_start + _CHUNK_SIZE, len(sections))

    # Strategy 1: precise slicing via plan markers.
    if vtt_timestamps:
        start_sec = None
        for s in sections[chunk_start:chunk_end]:
            if s.get("start_sec") is not None:
                start_sec = int(s["start_sec"])
                break
        if start_sec is not None:
            end_sec = None
            for s in sections[chunk_end:]:
                if s.get("start_sec") is not None:
                    end_sec = int(s["start_sec"])
                    break
            # Intermediate chunks need a known upper bound; only the LAST
            # chunk may extend to the video end. Otherwise fall through to
            # the deterministic length split.
            if end_sec is not None or chunk_end >= len(sections):
                return _slice_by_timestamps(vtt_timestamps, start_sec, end_sec)

    # Strategy 2: deterministic even length split — guarantees each chunk
    # receives only its part of the transcript (never the full one).
    if full_text:
        return _slice_by_length(full_text, chunk_idx, num_chunks)

    return None


def _generate_chunk(
    chunk_idx: int,
    num_chunks: int,
    sections: list[dict],
    plan_text: str,
    text: str,
    video_url: str,
    task_id: str,
    language: str = "es",
    mode: str = "unitema",
    vtt_timestamps: list | None = None,
    set_progress=None,
    step_idx: int = 0,
    steps: list | None = None,
    chunk_text: str | None = None,
) -> str:
    """Generate ONE detailed chunk (up to _CHUNK_SIZE sections).

    Uses a fresh OpenZEN session per chunk (avoids context accumulation
    timeout). Streams the model output to the task progress
    (``set_progress`` with ``stream_text``, throttled to ≤1 update/s)
    so the user sees the text being written live. Raises RuntimeError
    if the response is empty.

    Args:
        set_progress: Optional callable(task_id, step, detail, steps,
            stream_text=...) — used to persist streaming text to the DB.
        step_idx: Step number in the dynamic checklist for this chunk.
        steps: Dynamic steps list (from ``_build_dynamic_steps``).
        chunk_text: Optional transcript slice for THIS chunk (from
            ``_chunk_transcript_for``). When provided it replaces the full
            transcript in the user message so the model only sees the spoken
            range of its sections — avoids context degeneration on long
            videos. None → full transcript (current behavior).

    Returns:
        Sanitized markdown content for the chunk.
    """
    lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["es"])

    if _check_cancelled(task_id):
        raise RuntimeError("Cancelado por el usuario")
    if _retry_requested(task_id):
        # User hit retry-now before this chunk started → abort so it lands
        # in the recoverable chunk_error state instead of generating it.
        raise RuntimeError("Chunk reiniciado por petición del usuario")

    chunk_start = chunk_idx * _CHUNK_SIZE
    chunk_end = min(chunk_start + _CHUNK_SIZE, len(sections))
    chunk_sections = sections[chunk_start:chunk_end]

    section_lines = []
    for s in chunk_sections:
        desc_part = f" — {s['desc']}" if s["desc"] else ""
        section_lines.append(f"{s['num']}. **{s['title']}**{desc_part}")
    section_list = "\n".join(section_lines)

    chunk_prompt = _PROMPT_CHUNK_TEMPLATE.format(
        start=chunk_sections[0]["num"],
        end=chunk_sections[-1]["num"],
        chunk_num=chunk_idx + 1,
        total_chunks=num_chunks,
        outline=plan_text,
    )
    # por_tema uses the bounded format so frontend split stays at one
    # block per section instead of exploding into emoji sub-headings.
    format_rule = _FORMAT_RICH_POR_TEMA if mode == "por_tema" else _FORMAT_RICH
    system_prompt = f"{lang_instruction}\n\n{_FAITHFULNESS_RULE}\n\n{chunk_prompt}\n\n{format_rule}"

    # por_tema + vtt_timestamps → per-section timestamp links.
    # The plan-based chunks don't align with VTT ranges, so we sample the
    # whole list and let the AI pick the CLOSEST timestamp per section.
    ts_block = ""
    if mode == "por_tema" and vtt_timestamps:
        system_prompt += f"\n\n{_TIMESTAMP_INSTRUCTION}"
        ts_block = _build_timestamp_block(video_url, vtt_timestamps)

    user_message = (
        f"Here is the transcript from a YouTube video: {video_url}\n\n"
        "Generate the following sections of a comprehensive course note, "
        "based ONLY on the transcript below:\n\n"
        f"{chunk_text if chunk_text is not None else text}\n\n"
        f"Sections to generate:\n{section_list}"
        f"{ts_block}"
    )
    # Log the actual prompt size so context-degeneration issues are
    # diagnosable: a sliced chunk should be ~15x smaller than the full text.
    logger.info(
        "YouTubeZen task %s chunk %d/%d: prompt %d chars "
        "(full transcript %d chars, sliced=%s)",
        task_id, chunk_idx + 1, num_chunks, len(user_message),
        len(text), chunk_text is not None,
    )
    # Fresh session per chunk — avoids context accumulation timeout
    chunk_provider = get_provider("opencode-acp")

    # ── Streamed generation with live progress (throttled ≤1/s) ─────
    # The new stream_chat() yields incremental deltas; we accumulate
    # them in memory and persist a bounded tail (last ~12k chars) to
    # the task's error_message field at most once per second so the
    # frontend shows live text without hammering the DB.
    stream_buf: list[str] = []
    last_flush: dict = {"t": 0.0}
    last_poll: dict = {"t": 0.0}
    _STREAM_TAIL_LIMIT = 12000
    # Throttle persistence to ~3 updates/s during active generation: fast
    # enough to feel live (frontend polls at 500ms), slow enough to keep
    # DB writes cheap.
    _STREAM_FLUSH_INTERVAL = 0.35

    def _flush_stream(force: bool = False) -> None:
        if not set_progress:
            return
        now = time.monotonic()
        if not force and now - last_flush["t"] < _STREAM_FLUSH_INTERVAL:
            return
        last_flush["t"] = now
        tail = "".join(stream_buf)[-_STREAM_TAIL_LIMIT:]
        set_progress(
            task_id, step_idx,
            f"🤖 Generando chunk {chunk_idx + 1}/{num_chunks}...",
            steps=steps,
            stream_text=tail,
        )

    result: dict = {}
    try:
        gen = chunk_provider.stream_chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])
        # Heartbeat: seconds since the last delta. A stalled stream (no
        # tokens for _STREAM_IDLE_TIMEOUT_SECONDS) is aborted as a
        # recoverable failure instead of leaving the task 'processing'
        # forever. The companion SSE read-timeout in opencode_provider
        # covers the case where next(gen) blocks with NO deltas at all.
        last_delta_at = time.monotonic()
        # Consume the generator: collect deltas and capture the final
        # return value (StopIteration.value) which holds the result dict.
        while True:
            try:
                delta = next(gen)
            except StopIteration as stop:
                result = stop.value or {}
                break
            if delta:
                last_delta_at = time.monotonic()
                stream_buf.append(delta)
                _flush_stream()
            # Poll retry/cancel at a fixed cadence (same as the flush
            # throttle) — cheap single-row read, NOT on every delta.
            # Aborts the in-flight chunk so it lands in the recoverable
            # chunk_error state and the user can restart it at ANY time,
            # not just after a real failure. Also enforces the idle
            # heartbeat BETWEEN deltas (a provider that throttles to one
            # token per minute is as good as dead for our purposes).
            now = time.monotonic()
            if now - last_poll["t"] >= _STREAM_FLUSH_INTERVAL:
                last_poll["t"] = now
                if _check_cancelled(task_id):
                    raise RuntimeError("Cancelado por el usuario")
                if _retry_requested(task_id):
                    raise RuntimeError("Chunk reiniciado por petición del usuario")
                if now - last_delta_at > _STREAM_IDLE_TIMEOUT_SECONDS:
                    raise RuntimeError(
                        f"OpenZEN sin respuesta durante "
                        f"{_STREAM_IDLE_TIMEOUT_SECONDS}s en chunk "
                        f"{chunk_idx + 1} — reintenta o salta el chunk"
                    )
        _flush_stream(force=True)  # persist the tail right before validation
    except Exception as e:
        # Persist partial text even on failure so the user sees what was
        # generated before the error; then re-raise for retry logic.
        if stream_buf and set_progress:
            _flush_stream(force=True)
        raise

    content = result.get("content", "").strip()
    # Strip AI meta-text (chunk-retry/resume chatter) BEFORE validation so
    # the retry logic only triggers on genuinely empty/too-short content.
    content = _strip_ai_meta_text(content)
    if not content:
        raise RuntimeError(
            f"OpenZEN devolvió respuesta vacía en chunk {chunk_idx + 1}"
        )
    if len(content) < _MIN_CHUNK_CONTENT_CHARS:
        raise RuntimeError(
            f"OpenZEN devolvió contenido insuficiente "
            f"({len(content)} chars) en chunk {chunk_idx + 1}"
        )
    return _sanitize_output(content)


def _structure_detailed_with_ai(
    text: str,
    video_url: str,
    task_id: str,
    language: str = "es",
    set_progress=None,
    mode: str = "unitema",
    vtt_timestamps: list | None = None,
    template_id: str | None = None,
) -> str:
    """Detailed depth: plan pass → dynamic chunks → merge (port from KP).

    Args:
        text: Cleaned transcript text.
        video_url: Original YouTube URL (for context).
        task_id: Task id for cancellation checks.
        language: Output language ('es' or 'en').
        set_progress: Callable(task_id, step_idx, detail, steps) for progress.
        mode: 'unitema' (rich emoji headings) or 'por_tema' (one heading per
            section so frontend split stays bounded).
        vtt_timestamps: Optional list of (seconds, text) from the raw VTT.
            When mode='por_tema' this enables per-section timestamp links:
            the AI picks the CLOSEST sampled timestamp for every section.

    Returns:
        Merged structured markdown content.

    Raises:
        ChunkFailure: If a chunk call returns empty (recoverable — the caller
            persists intermediate state so the chunk can be retried).
        RuntimeError: If the plan call returns empty or the task is cancelled.
    """
    # Step 1: Plan pass (reused by retry-chunk / continue-without)
    plan_text, sections = _plan_detailed(text, video_url, task_id, language=language, template_id=template_id)

    num_chunks = max(1, (len(sections) + _CHUNK_SIZE - 1) // _CHUNK_SIZE)
    dynamic_steps = _build_dynamic_steps(plan_text, sections)

    parts: list[str] = []
    for chunk_idx in range(num_chunks):
        if set_progress:
            set_progress(task_id, 2 + chunk_idx, steps=dynamic_steps)
        try:
            chunk_text = _chunk_transcript_for(
                chunk_idx, num_chunks, sections, vtt_timestamps,
                full_text=text,
            )
            content = _generate_chunk(
                chunk_idx, num_chunks, sections, plan_text, text, video_url,
                task_id, language=language, mode=mode,
                vtt_timestamps=vtt_timestamps,
                set_progress=set_progress, step_idx=2 + chunk_idx,
                steps=dynamic_steps, chunk_text=chunk_text,
            )
        except RuntimeError as e:
            # Chunk failed (typically empty response) → raise recoverable
            # ChunkFailure so the caller saves state instead of losing the
            # healthy chunks already generated.
            raise ChunkFailure(
                chunk_idx=chunk_idx,
                num_chunks=num_chunks,
                parts=parts,
                plan_text=plan_text,
                sections=sections,
                message=str(e),
            ) from e
        parts.append(content)

    if _check_cancelled(task_id):
        raise RuntimeError("Cancelado por el usuario")

    if set_progress:
        set_progress(task_id, len(dynamic_steps) - 1, steps=dynamic_steps)
    return "\n\n---\n\n".join(parts)


# ── Background task ─────────────────────────────────────────────────

def _run_youtube_zen_task(
    task_id: str,
    url: str,
    topic_id: str,
    block_id: str,
    user_id: str,
    fmt: str = "markdown",
    depth: str = "standard",
    mode: str = "unitema",
    language: str = "es",
    template_id: str | None = None,
) -> None:
    """Background thread: YouTube → yt-dlp subtitles → OpenZEN structuring.

    Args:
        task_id: The ai_tasks row to update.
        url: YouTube video URL.
        topic_id: Topic to associate result with.
        block_id: Block to associate result with.
        user_id: User performing the action.
        fmt: Output format ('markdown' or 'html').
        depth: 'concise', 'standard' or 'detailed'.
        mode: 'unitema' or 'por_tema'.
        language: Output language ('es' or 'en').
        template_id: Optional template id from MD_TEMPLATES (NULL → legacy
            generic prompt behaviour).
    """
    try:
        db.execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )

        total_steps = len(_STEPS_YT_ZEN)

        # Step 0: Validate URL
        _set_progress(task_id, 0, "Validando URL...", steps=_STEPS_YT_ZEN)
        if not re.match(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/", url):
            raise ValueError(f"URL de YouTube no válida: {url}")

        # Step 1: Extract subtitles locally
        _set_progress(task_id, 1, "Descargando subtítulos con yt-dlp...", steps=_STEPS_YT_ZEN)
        raw_vtt = _extract_subtitles(url, lang=language)
        sub_chars = len(raw_vtt)
        _set_progress(task_id, 1, f"Subtítulos descargados ({sub_chars} chars VTT)", steps=_STEPS_YT_ZEN)

        # Video title (best-effort) → used for nav title + link callout in frontend
        video_title = _get_video_title(url)

        # Extract timestamps from raw VTT BEFORE cleaning (cleaning strips them)
        vtt_timestamps = _extract_timestamps_from_vtt(raw_vtt) if mode == "por_tema" else []

        # Step 2: Clean VTT
        _set_progress(task_id, 2, "Limpiando transcript...", steps=_STEPS_YT_ZEN)
        clean_text = _clean_vtt(raw_vtt)
        clean_chars = len(clean_text)
        noise_pct = round((1 - clean_chars / max(sub_chars, 1)) * 100)
        _set_progress(task_id, 2, f"Transcript limpio: {clean_chars} chars (ruido: {noise_pct}%)", steps=_STEPS_YT_ZEN)
        if not clean_text.strip():
            raise RuntimeError("El transcript extraído está vacío")

        # Step 3: AI structuring with OpenZEN
        if depth == "detailed":
            # Detailed mode: timestamps aren't chunk-aligned (plan-based
            # chunking doesn't align with VTT ranges) BUT for por_tema we
            # still pass the sampled VTT list so each section gets a link.
            _set_progress(task_id, 3, f"Planificando contenido detallado (~{clean_chars} chars)", steps=_STEPS_YT_ZEN)
            try:
                # Detailed mode + por_tema: pass VTT timestamps so the AI can
                # still attach a per-section timestamp link (closest sample).
                final_content = _structure_detailed_with_ai(
                    clean_text, url, task_id, language=language,
                    set_progress=_set_progress, mode=mode,
                    vtt_timestamps=vtt_timestamps,
                    template_id=template_id,
                )
                result_parts = [final_content]
            except ChunkFailure as cf:
                # A chunk returned empty → keep the healthy chunks + plan so
                # the user can retry just that chunk (or skip it / cancel)
                # instead of losing everything to a fatal error.
                _persist_chunk_failure(
                    task_id=task_id,
                    chunk_idx=cf.chunk_idx,
                    num_chunks=cf.num_chunks,
                    parts=cf.parts,
                    plan_text=cf.plan_text,
                    sections=cf.sections,
                    message=cf.message,
                    retry_count=0,
                    params={
                        "url": url,
                        "topic_id": topic_id,
                        "block_id": block_id,
                        "user_id": user_id,
                        "fmt": fmt,
                        "depth": depth,
                        "mode": mode,
                        "language": language,
                        "template_id": template_id,
                        "video_title": video_title,
                        "clean_chars": clean_chars,
                        "text": clean_text,
                        "vtt_timestamps": vtt_timestamps,
                    },
                )
                logger.warning(
                    "YouTubeZen task %s chunk %d/%d failed — saved recoverable state",
                    task_id, cf.chunk_idx + 1, cf.num_chunks,
                )
                return
        else:
            chunks = _chunk_text(clean_text)
            num_chunks = len(chunks)
            result_parts: list[str] = []

            # Map timestamps to chunks (only for standard/concise modes
            # where text splitting aligns with VTT timestamp ranges)
            chunk_timestamps = _map_chunk_timestamps(chunks, vtt_timestamps) if vtt_timestamps else []

            _set_progress(task_id, 3, f"Enviando a OpenZEN ({num_chunks} chunk{'s' if num_chunks > 1 else ''}, ~{clean_chars} chars)", steps=_STEPS_YT_ZEN)

            for i, chunk in enumerate(chunks):
                chunk_label = f"Chunk {i+1}/{num_chunks}" if num_chunks > 1 else "Procesando"
                _set_progress(task_id, 3, f"{chunk_label} — {len(chunk)} chars", steps=_STEPS_YT_ZEN)

                ts = chunk_timestamps[i] if i < len(chunk_timestamps) else None
                content = _structure_with_ai(
                    chunk, url, depth=depth, language=language,
                    mode=mode, timestamp=ts, template_id=template_id,
                )
                result_parts.append(content)

                ai_chars = sum(len(p) for p in result_parts)
                _set_progress(task_id, 3, f"{chunk_label} completado · {ai_chars} chars estructurados", steps=_STEPS_YT_ZEN)

            final_content = "\n\n".join(result_parts)

        # Step 4: Save
        _set_progress(task_id, 4, "Guardando resultado...", steps=_STEPS_YT_ZEN)
        _finalize_youtube_zen(
            task_id=task_id, url=url, block_id=block_id, fmt=fmt,
            depth=depth, mode=mode, language=language,
            video_title=video_title, clean_chars=clean_chars,
            final_content=final_content, result_parts=result_parts,
        )

    except Exception as e:
        logger.exception("YouTubeZen task %s failed", task_id)
        db.execute(
            "UPDATE ai_tasks SET status = 'error', error_message = %s, updated_at = NOW() WHERE id = %s",
            (str(e), task_id),
        )


def _set_progress(task_id: str, step: int, detail: str = "", steps=None,
                  pct: float | None = None, stream_text: str = ""):
    """Update task progress: checklist + detail + optional percentage.

    When ``stream_text`` is non-empty it is appended after a
    ``── STREAM ──`` separator, which the frontend renders as live
    streaming text (already wired in ``ai-tasks.js``).
    """
    if steps is None:
        steps = _STEPS_YT_ZEN
    from ai.generation.helpers import _build_progress, _build_data_block
    msg = _build_progress(step, detail, steps=steps)
    msg += _build_data_block(pct)
    if stream_text:
        msg += f"\n\n── STREAM ──\n{stream_text}"
    db.execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (msg, task_id),
    )


def _finalize_youtube_zen(
    task_id: str,
    url: str,
    block_id: str,
    fmt: str,
    depth: str,
    mode: str,
    language: str,
    video_title: str,
    clean_chars: int,
    final_content: str,
    result_parts: list[str],
) -> None:
    """Shared tail: HTML wrap (if needed) → save to block → mark task done.

    Used both by the initial run and by the resume thread so the finish
    pipeline stays in one place.
    """
    if fmt == "html":
        # v2 called `wrap_in_html_template`, which is never defined anywhere in
        # v2 — the HTML branch died with ImportError. The real helper is
        # `wrap_html_document`, as used by tasks_md.py / tasks_pdf.py.
        from ai.notebooklm.html_template import wrap_html_document
        _title = video_title or "StudyFlow YouTube"
        for _line in final_content.split("\n"):
            _line = _line.strip()
            if _line.startswith("# ") and not _line.startswith("##"):
                _title = _line[2:].strip()
                break
        final_content = wrap_html_document(final_content, title=_title)

    if block_id:
        db.execute(
            "UPDATE blocks SET content = %s, updated_at = NOW() WHERE id = %s",
            (final_content, block_id),
        )

    # Stats for frontend (depth/mode/language drive por_tema split)
    stats = {
        "title": url,
        "video_title": video_title,
        "input_chars": clean_chars,
        "output_chars": len(final_content),
        "depth": depth,
        "language": language,
        "mode": mode,
        "parts": len(result_parts),
        "model": "big-pickle via opencode-acp",
        "pipeline": "youtube-zen + openzen",
        "video_url": url,
    }

    db.execute(
        """UPDATE ai_tasks
           SET status = 'done', result_content = %s, coverage_data = %s,
               error_message = NULL, updated_at = NOW()
           WHERE id = %s""",
        (final_content, json.dumps(stats), task_id),
    )
    logger.info("YouTubeZen task %s completed: %d chars", task_id, len(final_content))


def _persist_chunk_failure(
    task_id: str,
    chunk_idx: int,
    num_chunks: int,
    parts: list[str],
    plan_text: str,
    sections: list[dict],
    message: str,
    retry_count: int,
    params: dict,
) -> None:
    """Store recoverable chunk-error state in coverage_data.

    The task stays in status='error' with error_message prefixed by
    ``CHUNK_ERROR|<chunk_idx>|<message>`` so the frontend can distinguish a
    recoverable chunk failure from a fatal error and offer retry / skip /
    cancel instead of just showing an error.
    """
    coverage = dict(params)
    coverage.update({
        "state": "chunk_error",
        "failed_chunk": chunk_idx,
        "num_chunks": num_chunks,
        "retry_count": retry_count,
        "max_retries": _MAX_CHUNK_RETRIES,
        "parts": parts,
        "plan_text": plan_text,
        "sections": sections,
    })
    # Phase 59 (issue #228): if the user cancelled while the chunk was
    # stalled/failing, do NOT overwrite 'cancelled' with 'error' — the
    # frontend should see the task disappear, not a CHUNK_ERROR zombie.
    # We still persist the recoverable state so a manual retry from the
    # UI works if the user changes their mind.
    if _check_cancelled(task_id):
        db.execute(
            """UPDATE ai_tasks
               SET coverage_data = %s, updated_at = NOW()
               WHERE id = %s""",
            (json.dumps(coverage, ensure_ascii=False), task_id),
        )
        logger.info(
            "YouTubeZen task %s chunk %d/%d failed AFTER user cancel — "
            "keeping status='cancelled' (recoverable state saved)",
            task_id, chunk_idx + 1, num_chunks,
        )
        return
    db.execute(
        """UPDATE ai_tasks
           SET status = 'error', error_message = %s, coverage_data = %s,
               updated_at = NOW()
           WHERE id = %s""",
        (f"CHUNK_ERROR|{chunk_idx}|{message}", json.dumps(coverage, ensure_ascii=False), task_id),
    )


def _resume_detailed_task(
    task_id: str,
    start_chunk: int,
    skip_chunk: int | None,
    retry_count: int,
) -> None:
    """Background thread: resume a failed detailed generation.

    Reuses the plan / sections / healthy parts stored in coverage_data,
    regenerates chunks from ``start_chunk`` (skipping ``skip_chunk`` if set),
    and finishes the pipeline (save to block + mark done). On another chunk
    failure it re-persists the state with the given ``retry_count``.

    Progress uses the SAME per-chunk checklist as the initial detailed run
    (via _build_dynamic_steps) so the user sees each chunk with its section
    range and which chunks are already done (✅) vs pending (☐) while resuming.
    """
    try:
        db.execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        row = db.query_one(
            "SELECT coverage_data FROM ai_tasks WHERE id = %s",
            (task_id,),
        )
        if not row or not row["coverage_data"]:
            raise RuntimeError("No hay estado intermedio para reanudar")
        state = json.loads(row["coverage_data"])
        if state.get("state") != "chunk_error":
            raise RuntimeError("La tarea no está en estado reanudable")
        # Clear any stale retry_requested flag before resuming — otherwise the
        # FIRST chunk of this resume would abort immediately (a retry-now that
        # arrived after the previous chunk had already completed) → infinite
        # retry loop until the limit is exhausted.
        if state.pop("retry_requested", None) is not None:
            db.execute(
                "UPDATE ai_tasks SET coverage_data = %s WHERE id = %s",
                (json.dumps(state, ensure_ascii=False), task_id),
            )

        plan_text = state["plan_text"]
        sections = state["sections"]
        num_chunks = int(state["num_chunks"])
        parts = list(state.get("parts") or [])
        text = state.get("text", "")
        video_url = state.get("url", "")
        language = state.get("language", "es")
        mode = state.get("mode", "unitema")
        vtt_timestamps = state.get("vtt_timestamps") or []
        fmt = state.get("fmt", "markdown")
        block_id = state.get("block_id")
        depth = state.get("depth", "detailed")
        video_title = state.get("video_title", "")
        clean_chars = int(state.get("clean_chars", 0))

        # Same per-chunk checklist as the initial detailed run: each chunk
        # gets its own step (with section range). Because the checklist is
        # positional, already-done chunks (those < start_chunk, already in
        # `parts`) appear as ✅ and the one being regenerated as ⏳.
        dynamic_steps = _build_dynamic_steps(plan_text, sections)

        for chunk_idx in range(start_chunk, num_chunks):
            if skip_chunk is not None and chunk_idx == skip_chunk:
                continue
            if _check_cancelled(task_id):
                raise RuntimeError("Cancelado por el usuario")
            _set_progress(
                task_id, 2 + chunk_idx, steps=dynamic_steps,
            )
            try:
                chunk_text = _chunk_transcript_for(
                    chunk_idx, num_chunks, sections, vtt_timestamps,
                    full_text=text,
                )
                content = _generate_chunk(
                    chunk_idx, num_chunks, sections, plan_text, text, video_url,
                    task_id, language=language, mode=mode,
                    vtt_timestamps=vtt_timestamps,
                    set_progress=_set_progress, step_idx=2 + chunk_idx,
                    steps=dynamic_steps, chunk_text=chunk_text,
                )
            except RuntimeError as e:
                _persist_chunk_failure(
                    task_id=task_id, chunk_idx=chunk_idx, num_chunks=num_chunks,
                    parts=parts, plan_text=plan_text, sections=sections,
                    message=str(e), retry_count=retry_count, params=state,
                )
                logger.warning(
                    "YouTubeZen resume task %s chunk %d/%d failed (retry %d) — saved state",
                    task_id, chunk_idx + 1, num_chunks, retry_count,
                )
                return
            parts.append(content)

        final_content = "\n\n---\n\n".join(parts)
        result_parts = [final_content]
        _set_progress(task_id, len(dynamic_steps) - 1, steps=dynamic_steps)
        _finalize_youtube_zen(
            task_id=task_id, url=video_url, block_id=block_id, fmt=fmt,
            depth=depth, mode=mode, language=language,
            video_title=video_title, clean_chars=clean_chars,
            final_content=final_content, result_parts=result_parts,
        )
    except Exception as e:
        if "Cancelado por el usuario" in str(e):
            db.execute(
                "UPDATE ai_tasks SET status = 'cancelled', error_message = NULL, updated_at = NOW() WHERE id = %s",
                (task_id,),
            )
            return
        logger.exception("YouTubeZen resume task %s failed", task_id)
        db.execute(
            "UPDATE ai_tasks SET status = 'error', error_message = %s, updated_at = NOW() WHERE id = %s",
            (str(e), task_id),
        )


# ── Public API ──────────────────────────────────────────────────────

def create_youtube_zen_task(
    url: str,
    topic_id: str,
    block_id: str,
    user_id: str,
    fmt: str = "markdown",
    depth: str = "standard",
    mode: str = "unitema",
    language: str = "es",
    template_id: str | None = None,
) -> dict:
    """Create a YouTubeZen background task.

    Args:
        url: YouTube video URL.
        topic_id: Topic to associate result with.
        block_id: Block to update with result.
        user_id: User creating the task.
        fmt: Output format ('markdown' or 'html').
        depth: 'concise', 'standard' or 'detailed'.
        mode: 'unitema' or 'por_tema'.
        language: Output language ('es' or 'en').
        template_id: Optional template id from MD_TEMPLATES (NULL → legacy).

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails.
    """
    if not url:
        raise ValueError("Missing required field: url")
    if not re.match(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/", url):
        raise ValueError(f"URL de YouTube no válida: {url}")

    if depth not in ("concise", "standard", "detailed"):
        raise ValueError(f"Profundidad no válida: {depth}")
    if language not in ("es", "en"):
        raise ValueError(f"Idioma no válido: {language}")
    if mode not in ("unitema", "por_tema"):
        raise ValueError(f"Modo no válido: {mode}")
    if template_id is not None and template_id not in MD_TEMPLATES:
        raise ValueError(f"Plantilla no válida: {template_id}")

    # v3 topics/blocks are SERIAL integers — a "" from the frontend would hit
    # the integer column as an empty string and raise InvalidTextRepresentation.
    topic_id = _coerce_id(topic_id, "topic_id")
    block_id = _coerce_id(block_id, "block_id")

    # Phase 58 (issue #225): tasks are no longer processed by a per-task
    # thread — a single FIFO daemon worker (youtube_queue) picks them up in
    # order. url/mode go into coverage_data so the worker can reconstruct the
    # full parameter set from the row; depth lives in the `length` column,
    # language in the `language` column (both exist in ai_tasks).
    # v3's ai_tasks.id has NO default (v2 generated it server-side), so the
    # INSERT must supply it or the row dies with a NotNullViolation — the
    # same trap already fixed in youtube.py.
    task_row = db.execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status,
            template_id, language, length, coverage_data)
           VALUES (%s, %s, %s, 'youtube_zen', %s, 'youtube', %s, 'queued', %s, %s, %s, %s)
           RETURNING id""",
        (
            _new_task_id(),
            user_id, topic_id, fmt, block_id, template_id,
            language, depth,
            json.dumps({"url": url, "mode": mode}, ensure_ascii=False),
        ),
    )
    task_id = task_row["id"]

    # Wake the FIFO worker (idempotent start) — it processes the queue in
    # order and calls _run_youtube_zen_task itself.
    from ai.notebooklm import youtube_queue  # late import (avoid cycle)
    youtube_queue.ensure_running()
    youtube_queue.wake()

    return {"task_id": task_id}


def resume_youtube_zen_task(task_id: str, user_id: str, action: str) -> dict:
    """Resume a failed youtube_zen detailed task (chunk error).

    Args:
        task_id: The ai_tasks row to resume.
        user_id: The user requesting the resume (ownership check).
        action: 'retry' → regenerate the failed chunk and continue the rest;
                'skip' → continue without the failed chunk.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: Task not found / not resumable / retries exhausted.
    """
    row = db.query_one(
        "SELECT status, coverage_data FROM ai_tasks WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )
    if not row:
        raise ValueError("Tarea no encontrada")
    if row["status"] != "error":
        raise ValueError("La tarea no está en estado de error reanudable")
    state = json.loads(row["coverage_data"] or "{}")
    if state.get("state") != "chunk_error":
        raise ValueError("La tarea no tiene estado de chunk reanudable")

    failed_chunk = int(state["failed_chunk"])
    num_chunks = int(state["num_chunks"])
    retry_count = int(state.get("retry_count", 0))
    # Use the *current* configured limit — the state may carry an older
    # frozen max_retries (e.g. 3) from before a limit bump; the higher
    # value wins so already-failed tasks keep working with more headroom.
    max_retries = max(
        int(state.get("max_retries", _MAX_CHUNK_RETRIES)),
        _MAX_CHUNK_RETRIES,
    )

    if action == "retry":
        if retry_count >= max_retries:
            raise ValueError(
                f"Límite de reintentos alcanzado ({retry_count}/{max_retries}). "
                "Solo puede continuar sin el chunk o cancelar."
            )
        next_retry = retry_count + 1
        start_chunk = failed_chunk
        skip_chunk = None
    elif action == "skip":
        next_retry = retry_count
        start_chunk = failed_chunk + 1
        skip_chunk = failed_chunk
    else:
        raise ValueError(f"Acción no válida: {action}")

    thread = threading.Thread(
        target=_resume_detailed_task,
        args=(task_id, start_chunk, skip_chunk, next_retry),
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id}


def request_chunk_retry(task_id: str, user_id: str) -> dict:
    """Ask the RUNNING detailed task to abort the current chunk (retry-now).

    Flips ``coverage_data.retry_requested`` to True while the task is still
    'processing'. The streaming loop in :func:`_generate_chunk` polls this
    flag (cheap single-row read, ~0.35s cadence) and aborts the in-flight
    chunk so it lands in the recoverable chunk_error state — the user can
    then retry-chunk it immediately, not just after a real failure.

    The flag is cleared by the resumer (:func:`_resume_detailed_task`) before
    it regenerates, so a stale flag can never abort the FIRST chunk of a
    resume (which would otherwise cause an infinite retry loop).

    Args:
        task_id: The ai_tasks row to flag.
        user_id: The user requesting it (ownership check).

    Returns:
        {"task_id": str}

    Raises:
        ValueError: Task not found / not owned / not currently processing.
    """
    row = db.query_one(
        "SELECT status, coverage_data FROM ai_tasks "
        "WHERE id = %s AND user_id = %s AND task_type = 'youtube_zen'",
        (task_id, user_id),
    )
    if not row:
        raise ValueError("Tarea no encontrada")
    if row["status"] != "processing":
        raise ValueError(
            "La tarea no está en ejecución; no puede reiniciarse un chunk ahora"
        )
    state = json.loads(row["coverage_data"] or "{}")
    state["retry_requested"] = True
    db.execute(
        "UPDATE ai_tasks SET coverage_data = %s, updated_at = NOW() WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
    return {"task_id": task_id}
