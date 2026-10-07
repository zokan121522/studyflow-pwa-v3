"""
Vocabulary Suite (backend) — ported from studyflow-hub v2 (Phase 70, #261).

Self-contained ``vocabulary-suite.html`` from a textarea of user-typed
words/phrases (one per line). Each line →
``{word, phrase, meaning, translate}`` via a single LLM call, then
injected at the ``var VOCAB_DATA=[];`` seam.

- ``word``    = student term, minimally corrected (drills the word itself)
- ``phrase``  = new natural example sentence containing the word (TTS-able)
- ``meaning`` = English gloss
- ``translate`` = Spanish translation

The model also returns a short THEMATIC title (``_meta`` first array
element) that relates all the words of the batch; the backend extracts it
into its own ``var VOCAB_TITLE="…"`` seam so the HTML header/report never
look generic.

Provider-parametric (opencode-acp default | notebooklm). No NotebookLM
pre-condensation: the payload is small + student-curated.

v3 adaptations vs v2 (verified, not assumed):
  1. imports drop the ``backend.`` prefix (v3 packages are flat).
  2. ai_tasks INSERT supplies ``id`` via ``_new_task_id()`` — the v3
     ``ai_tasks.id`` column is text with NO DEFAULT, so v2's INSERT 500s.
  3. ``source_id`` is text in v3, so ``course_id or ""`` is still valid.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import database as db
from ai.generation.grammar import (
    _GRAMMAR_LABEL,
    _GRAMMAR_PROVIDERS,
    _run_llm_with_retry,
    _set_progress,
)
from ai.generation.helpers import inject_provenance, provenance_banner
from ai.notebooklm.utils import _new_task_id

ASSET_TEMPLATE = Path(__file__).resolve().parent / "assets" / "vocabulary-suite.html"
_MAX_INPUT_LINES = 120
_MAX_FIELD_CHARS = 120
_DEFAULT_SIZE = 10


# ─── JSON array helper ──────────────────────────────────────────────────────
# Array-aware sibling of grammar._extract_json. Grammar's helper is
# dict-only (returns None for valid arrays, which we observed in
# production: even a perfect answer failed as "JSON no es un array").
# The vocab prompt requires an ARRAY, so we keep the same fence stripping
# but accept `list` (or a single-key dict wrapper like {"items": [...]})
# instead of dict.
def _extract_json_array(text):
    t = (text or "").strip()
    if not t:
        return None
    # 1) Strip ``` fences (same regex style as grammar).
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()
    # 2) Whole-string parse: bare list / single-key dict wrapper.
    try:
        parsed = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and len(parsed) == 1:
        only = next(iter(parsed.values()))
        if isinstance(only, list):
            return only
    # 3) Fallback: slice the outermost [...] (handles intro/outro prose).
    s, e = t.find("["), t.rfind("]")
    if s != -1 and e > s:
        try:
            parsed = json.loads(t[s:e + 1])
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, list):
            return parsed
    return None


def _trunc(s, cap=_MAX_FIELD_CHARS):
    s = (s or "").strip()
    return s if len(s) <= cap else s[: max(1, cap - 1)] + "…"


# ─── Template injection ─────────────────────────────────────────────────────
def _load_template():
    html = ASSET_TEMPLATE.read_text(encoding="utf-8")
    if "var VOCAB_DATA=" not in html:
        raise RuntimeError("Template corrupt: falta el seam 'var VOCAB_DATA='")
    return html


def _inject_vocab_data(html, items, title=None):
    payload = json.dumps(items, ensure_ascii=False)
    if title:
        payload_t = json.dumps(str(title), ensure_ascii=False)
        seam_t = re.compile(
            r"^(\s*)var VOCAB_TITLE=\".*?\";\s*(?:/\*VOCAB_INJECT_TITLE\*/\s*)?$", re.MULTILINE
        )
        html, n = seam_t.subn(
            lambda m: f"{m.group(1)}var VOCAB_TITLE={payload_t};/*VOCAB_INJECT_TITLE*/", html
        )
        if n != 1:
            raise RuntimeError("Vocabulary title seam not found (expected exactly 1 match)")
    seam = re.compile(r"^(\s*)var VOCAB_DATA=\[.*?\];\s*(?:/\*VOCAB_INJECT\*/\s*)?$", re.MULTILINE)
    new_html, n = seam.subn(lambda m: f"{m.group(1)}var VOCAB_DATA={payload};/*VOCAB_INJECT*/", html)
    if n != 1:
        raise RuntimeError("Vocabulary seam not found (expected exactly 1 match)")
    return new_html


# ─── Prompts ─────────────────────────────────────────────────────────────────
_GENERIC_TITLE_WORDS = {
    "vocabulary", "words", "word", "mixed", "mix", "daily", "random",
    "english", "list", "practice", "vocab", "phrases", "phrase", "basic",
    "common", "everyday", "learn",
}


def _title_is_usable(t):
    """Keep the model title unless every token is generic filler."""
    t = (t or "").strip()
    if len(t) < 3:
        return False
    return any(w not in _GENERIC_TITLE_WORDS for w in t.lower().split())


def _compose_title(title, topics):
    """Deterministic themed title for a batch of words.

    - 2+ distinct topics → connecting phrase of the real domains:
      ``Food, work and travel`` (never generic, never forced).
    - 1 topic → the model's title, unless generic/blank, else
      ``<Topic> essentials``.
    - Returns "" when nothing usable → caller keeps the legacy generic header.
    """
    ts = [str(t).strip().title() for t in (topics or []) if str(t).strip()]
    seen, uniq = set(), []
    for t in ts:  # dedupe, keep order, cap at 4
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
        if len(uniq) >= 4:
            break
    if len(uniq) >= 2:
        parts = [uniq[0]] + [u.lower() for u in uniq[1:]]
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    if len(uniq) == 1:
        if _title_is_usable(title):
            return " ".join(title.split())[:80]
        return uniq[0] + " essentials"
    if _title_is_usable(title):
        return " ".join(title.split())[:80]
    return ""


def _system_prompt():
    return (
        "You are an expert English vocabulary coach for Spanish-speaking learners (CEFR B1-C1). "
        "The student gives you a list of words/phrases they want to drill; you output ONE JSON array — nothing else. "
        "No markdown fences, no comments, no extra prose.\n\n"
        "STRICT RULES:\n"
        "- Output ONLY a JSON array. First char '[', last ']'.\n"
        "- The FIRST element is a meta object: {\"_meta\": {\"title\": \"…\", \"topics\": [\"…\"]}}. The meta has exactly TWO keys: 'title' (string) and 'topics' (array of strings).\n"
        "- 'topics' = 1 to 4 short English categories (singular nouns, lowercase, no quotes) that TOGETHER COVER ALL input words. Prefer the FEWEST that fit. Examples: [\"food\"], [\"food\", \"travel\"], [\"work\", \"home\", \"health\"].\n"
        "- 'title' = if ONE topic covers everything → that theme phrased nicely (e.g. \"Airport and travel essentials\", \"Job interview phrases\"). If TWO OR MORE topics → a connecting phrase that LISTS them (e.g. \"Food and travel\", \"Work, home and health\"). NEVER output generic or forced titles (no \"Vocabulary\", \"Words\", \"Mixed\", \"Daily\", \"Random\", \"Everyday\", \"Common\").\n"
        "- 'title': British English, 3–6 words, NO quotes, NO emoji. 'topics' values: lowercase, singular.\n"
        "- After the meta object, EXACTLY one object per input line, SAME ORDER, SAME COUNT. Total elements = 1 (meta) + N, where N = number of input lines.\n"
        "- Each word object has exactly FOUR string keys: 'word', 'phrase', 'meaning', 'translate'. No other keys, no nulls, no numbers.\n"
        "- 'word' = the student's text minimally corrected (typos/casing; never add articles or change a known idiom).\n"
        "- 'phrase' = a NEW natural example sentence in British English containing 'word', 6–14 words, CEFR B1–B2, suitable for TTS.\n"
        "- 'meaning' = concise English gloss, <=90 chars.\n"
        "- 'translate' = natural Spanish translation, <=90 chars.\n"
        "- No newlines inside strings. Escape inner double-quotes with \\\"."
    )


def _user_prompt(lines):
    numbered = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(lines))
    return f"Produce the JSON array for these {len(lines)} input lines (same order, same count):\n\n{numbered}"


def _validate(items, expected):
    """Returns (cleaned_items, None) or (None, reason).

    Each item has four keys: word/phrase/meaning/translate. Backward compat:
    legacy 3-key items (phrase/meaning/translate, no 'word') are accepted by
    promoting 'phrase' into 'word' and leaving the example 'phrase' empty.

    The model may prepend a meta object {"_meta": {title, topics}}. The
    backend composes a deterministic title via _compose_title() (never
    generic, never forced) and re-prepends it as a single {"_title": …}
    marker so the caller can extract it without breaking the
    (list | None, reason) contract. If the meta is absent (or unusable),
    no marker is added (legacy OK).
    """
    if not isinstance(items, list):
        return None, "JSON no es un array"
    final_title = ""
    if items and isinstance(items[0], dict) and "_meta" in items[0]:
        meta = items[0].get("_meta")
        title, topics = "", []
        if isinstance(meta, dict):
            title = str(meta.get("title", "") or "").strip()
            tp = meta.get("topics")
            if isinstance(tp, list):
                topics = tp
        final_title = _compose_title(title, topics)
        items = items[1:]
        if len(items) != expected:
            return None, f"len con _meta = {len(items) + 1} != expected {expected} + 1"
    elif len(items) != expected:
        return None, f"len={len(items)} != expected {expected}"
    cleaned = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return None, f"item #{idx + 1} no es un objeto"
        w = str(item.get("word", "")).strip()
        p = str(item.get("phrase", "")).strip()
        m = str(item.get("meaning", "")).strip()
        t = str(item.get("translate", "")).strip()
        # legacy fallback: missing word → word = old phrase
        if not w and p:
            w = p
        if not p:
            p = ""
        if not (w and m and t):
            return None, f"item #{idx + 1} tiene campo(s) vacío(s)"
        cleaned.append(
            {
                "word": _trunc(w),
                "phrase": _trunc(p, _MAX_FIELD_CHARS * 2),
                "meaning": _trunc(m),
                "translate": _trunc(t),
            }
        )
    if final_title:
        cleaned = [{"_title": final_title}] + cleaned
    return cleaned, None


# ─── Task lifecycle ──────────────────────────────────────────────────────────
def create_vocabulary_task(
    words_text, *, topic_id, user_id, course_id=None,
    provider="opencode-acp", size=None,
):
    if not isinstance(words_text, str) or not words_text.strip():
        raise ValueError("words_text no puede estar vacío")
    lines = [ln.strip() for ln in words_text.splitlines() if ln.strip()]
    if not (1 <= len(lines) <= _MAX_INPUT_LINES):
        raise ValueError(f"Número de líneas fuera de rango: {len(lines)} (1..{_MAX_INPUT_LINES})")
    if not topic_id or not user_id:
        raise ValueError("Missing required fields: topic_id, user_id")
    if provider not in _GRAMMAR_PROVIDERS:
        raise ValueError(
            f"Invalid provider: '{provider}'. Must be one of: {', '.join(sorted(_GRAMMAR_PROVIDERS))}"
        )
    # Toolbar can send "all" or a numeric size; coerce defensively.
    size_norm = "all" if size == "all" else int(size or _DEFAULT_SIZE)
    if size_norm == "all":
        size_norm = max(len(lines), _DEFAULT_SIZE)
    # v3's ai_tasks.id has NO default; without it this INSERT always 500s.
    task_row = db.execute_returning(
        """INSERT INTO ai_tasks (id, user_id, topic_id, task_type, source_type, source_id, model_used, status)
           VALUES (%s, %s, %s, 'vocabulary', 'topic', %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id, course_id or "", provider),
    )
    task_id = task_row["id"]
    threading.Thread(
        target=_run_vocabulary_generation,
        args=(task_id, lines, provider, size_norm, course_id or ""),
        daemon=True,
    ).start()
    return {"task_id": task_id}


def _run_vocabulary_generation(task_id, lines, provider_name, size_norm, course_id):
    try:
        db.execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _set_progress(task_id, f"Enriqueciendo {len(lines)} frases con el coach…")

        items = _run_llm_with_retry(
            _system_prompt(), _user_prompt(lines),
            lambda raw: _validate(_extract_json_array(raw), len(lines)),
            "vocab array", provider_name,
        )
        # Thematic title: stripped from the head marker, injected into its
        # own VOCAB_TITLE seam so the header/report are themed.
        title = None
        if items and isinstance(items[0], dict) and "_title" in items[0]:
            title = items.pop(0)["_title"]
        # Toolbar round size wins; hard cap keeps the iframe sane.
        if size_norm and 0 < size_norm < len(items):
            items = items[:size_norm]
        if len(items) > _MAX_INPUT_LINES:
            items = items[:_MAX_INPUT_LINES]
        for i, it in enumerate(items):
            it["id"] = i  # match game references items by id

        html = _inject_vocab_data(_load_template(), items, title)
        counts = f"{len(items)} frases"
        if size_norm:
            counts += f" · ronda {size_norm}"
        prov = provenance_banner(
            "vocabulary-suite.html", _GRAMMAR_LABEL.get(provider_name, provider_name), "", counts=counts
        )
        html = inject_provenance(html, prov)
        model_label = _GRAMMAR_LABEL.get(provider_name, provider_name)
        stats = {
            "items": len(items), "rounds": "single-call",
            "provider": provider_name, "model": model_label,
            "pipeline": "vocabulary-suite + ai (single call)",
            "max_field_chars": _MAX_FIELD_CHARS, "course_id": course_id,
        }
        done_msg = f"📚 Vocabulary Suite generada · {len(items)} frases · {model_label}"
        # Anti-zombie: si fue cancelada, no pisar el estado.
        row = db.query_one("SELECT status FROM ai_tasks WHERE id = %s", (task_id,))
        if row and row["status"] != "processing":
            return
        db.execute(
            "UPDATE ai_tasks SET status = 'done', result_content = %s, coverage_data = %s, "
            "error_message = %s, completed_at = NOW(), updated_at = NOW() WHERE id = %s",
            (html, json.dumps(stats, ensure_ascii=False), done_msg, task_id),
        )
    except Exception as e:
        row = db.query_one("SELECT status FROM ai_tasks WHERE id = %s", (task_id,))
        if row and row["status"] != "processing":
            return
        db.execute(
            "UPDATE ai_tasks SET status = 'error', error_message = %s, "
            "completed_at = NOW(), updated_at = NOW() WHERE id = %s",
            (str(e), task_id),
        )
