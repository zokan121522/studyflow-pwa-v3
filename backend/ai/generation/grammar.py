"""
English Grammar Exercises — Phase 61 addon (backend).

Generates a practice CONFIG for ``english-practice.html`` using OpenZEN
(local opencode-acp / big-pickle). The template is stored in
``backend/ai/generation/assets/english-practice.html`` and is rendered fully
client-side inside a sandboxed <iframe> (InteractiveBlocks); this module only:

    1. validates the source block (markdown/content; PDF out of scope in v1),
    2. INSERTs an ``ai_tasks`` row (task_type ``generate_grammar``),
    3. spawns a background thread that prompts OpenZEN for the CONFIG,
    4. validates + injects the CONFIG into the template and stores the result.

Generation (multi-call + merge, YouTube Zen pattern — v1.1):
    per_type <= 5    → 1 single call producing every type (small pool)
    per_type >= 10   → 7 calls: call 1 = metadata (title/subtitle/tags/
                       MEANINGS) + ``choose``; calls 2..7 = one type each.
                       Each call uses a fresh provider session (no history
                       accumulation); pools are merged and ids re-indexed.
                       A type block that fails validation after a retry is
                       SKIPPED (its section is removed) so the practice still
                       completes with the healthy blocks.

Flow:
    create_grammar_task()      — synchronous task creator (request context)
    _run_grammar_generation()  — background thread (OpenZEN + inject + store)
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import database as db
from ai.generation.helpers import provenance_banner, inject_provenance, _PROV_DEPTH
from ai.providers import get_provider
from ai.notebooklm.utils import _new_task_id

ASSET_TEMPLATE = Path(__file__).resolve().parent / "assets" / "english-practice.html"

# Source scopes supported by the addon in v1 (S6: PDF out of scope).
SUPPORTED_SOURCE_TYPES = ("markdown", "content")

_EXERCISE_TYPES = ("choose", "order", "match", "error", "tf", "transform", "listen")
_VALID_PER_TYPE = (1, 5, 10, 20, 40)

# Opciones del selector "Por apartado" del template según el nivel elegido.
# El diálogo solo ofrece la extensión; la cantidad queda implícita aquí.
_SIZE_OPTIONS = {1: [1], 5: [1, 5], 10: [1, 5, 10], 20: [10, 20], 40: [20, 40]}
_DEFAULT_PER_TYPE = 10
_MAX_MATERIAL_CHARS = 16_000
_MAX_CHUNK_RETRIES = 1
_MULTI_CALL_THRESHOLD = 5  # per_type <= 5 → single call with all types

# Phase 66 (#257) — provider parametric. Allowlist mirrors backend/routes/ai.py.
# "opencode-acp" is the OpenZen sidecar, already registered in ai.providers;
# the rest of the pipeline was provider-parametric from the start (the
# NotebookLM-only branches are gated on provider_name), it was just never
# allowlisted here — so the OpenZen button had nothing to call.
_GRAMMAR_PROVIDERS = {"notebooklm", "opencode-acp"}
_GRAMMAR_LABEL = {"opencode-acp": "OpenZen (opencode-acp)", "notebooklm": "NotebookLM (Gemini)"}

# Phase 68 (#259) — NotebookLM pre-condensation (line-greedy slice digest).
# Six input slices of <=2600 chars, each condensed to ~366 chars (6*366=2196<=2200),
# joined budget 2200, absolute ceiling 3000 after hard-truncate.
_NB_SLICE_CHARS = 2600
_NB_SLICE_CAP = 6
_NB_MATERIAL_BUDGET = 2200
_NB_HARD_CAP = 3000

# ─── Section descriptors (fixed) ─────────────────────────────────────

_SECTION_DEFS = [
    ("choose", "Choose", "Choose the correct ..."),
    ("order", "Order", "Tap the words to build the sentence"),
    ("match", "Match", "Match each ..."),
    ("error", "Spot the Error", "Choose the correct sentence"),
    ("tf", "Correct?", "Is this sentence grammatically correct?"),
    ("transform", "Transform", "Transform the sentence"),
    ("listen", "Listen", "Listen and choose what you heard"),
]

# ─── Prompt fragments ────────────────────────────────────────────────

_TYPE_SHAPES = {
    "choose": (
        "- choose: {id,type:'choose',s,a,opts:[4],tell,explain} — s a sentence with a "
        "___ placeholder; a the correct answer; opts[0] MUST equal a (the app shuffles); "
        "tell the full correct sentence; explain in one English line."
    ),
    "order": (
        "- order: {id,type:'order',pieces,tell,explain} — `tell` is the full correct "
        "sentence (natural capitals/punctuation); `pieces` MUST be the words of `tell` "
        "IN CORRECT SENTENCE ORDER, 4..6 tokens, each lowercase and WITHOUT punctuation "
        "(proper nouns allowed) — the app itself shuffles pieces for the user, so pieces "
        "order IS the expected answer; explain in one short line (may be Spanish)."
    ),
    "match": (
        "- match: {id,type:'match',a,mean} — a surface form (verb/word); mean its past "
        "form or meaning."
    ),
    "error": (
        "- error: {id,type:'error',wrong,a,opts:[4],tell,explain} — `wrong` is the incorrect "
        "sentence shown to the user; `a` is the CORRECTED sentence in natural English; "
        "`opts[0]` MUST equal `a` (the app uses opts[0] as the canonical corrected form); "
        "`tell` MUST be the corrected English sentence in plain natural English (the 🔊 "
        "TTS button reads this aloud — NEVER an explanation or Spanish text); "
        "`explain` is a short rationale and MAY be in Spanish."
    ),
    "tf": (
        "- tf: {id,type:'tf',s,a:true|false,tell,explain} — `s` is the sentence the user "
        "evaluates; `a` (bool) is whether it is grammatically correct; `tell` MUST be the "
        "corrected/target version in natural English when false, or the original `s` when "
        "true (the 🔊 TTS button reads `tell` aloud — NEVER an explanation or Spanish "
        "text); `explain` is a short rationale and MAY be in Spanish (may start with "
        "'CORRECTA.' / 'INCORRECTA.')."
    ),
    "transform": (
        "- transform: {id,type:'transform',pieces,instruction,base,a,s,tell,explain} — "
        "s: the sentence to transform (e.g. present -> past); a: the CORRECT transformed "
        "sentence; pieces: 4..6 lowercase tokens of `a` WITHOUT punctuation or capitals "
        "(proper nouns allowed) — the app rebuilds the sentence by ordering them; "
        "instruction: short English order (e.g. 'Change to simple past'); base: identical "
        "to s; `tell` MUST be the transformed English sentence in plain natural English "
        "(the 🔊 TTS button reads `tell` aloud — NEVER an explanation or Spanish text); "
        "`explain` is a short rationale and MAY be in Spanish."
    ),
    "listen": (
        "- listen: {id,type:'listen',s,a,tell,explain} — s the sentence the TTS reads "
        "aloud; a the correct transcription choice (short, natural)."
    ),
}

_SECTIONS_JSON_TEXT = (
    "[{type:'choose',num:1,title:'Choose',sub:'Choose the correct ...'},\n"
    " {type:'order',num:2,title:'Order',sub:'Tap the words to build the sentence'},\n"
    " {type:'match',num:3,title:'Match',sub:'Match each ...'},\n"
    " {type:'error',num:4,title:'Spot the Error',sub:'Choose the correct sentence'},\n"
    " {type:'tf',num:5,title:'Correct?',sub:'Is this sentence grammatically correct?'},\n"
    " {type:'transform',num:6,title:'Transform',sub:'Transform the sentence'},\n"
    " {type:'listen',num:7,title:'Listen',sub:'Listen and choose what you heard'}]"
)

_QUALITY_BAR = (
    "- Sentences short (3..9 words), natural, textbook-correct English.\n"
    "- Every exercise must reuse the material's vocabulary and grammar.\n"
    "- Properly escaped strings; no newlines inside strings.\n"
    "- Use ONLY the listed keys (no extras)."
)


def _system_meta_prompt(per_type: int) -> str:
    """Chunk 1 system prompt: metadata + the 'choose' block."""
    return (
        "You are an expert English grammar exercise designer for Spanish-speaking "
        "learners (CEFR A1-B1). You will receive study material and must produce a "
        "JSON object consumed by a practice web app.\n\n"
        "RULES:\n"
        "- Output ONLY valid JSON. No markdown fences, no comments, no extra text.\n"
        '- The object has exactly 5 keys: "title" (short thematic title with an emoji), '
        '"subtitle" (one English sentence describing the practice), "tags" (array of max '
        '15 key vocabulary/grammar words), "MEANINGS" (object mapping key words to short '
        'Spanish glosses, max 25 entries), "POOL" (a list of EXACTLY ' + str(per_type) +
        ' items of type "choose").\n\n'
        '"choose" ITEM SHAPE:\n'
        + _TYPE_SHAPES["choose"] + "\n\n"
        "QUALITY BAR:\n"
        "- Exactly " + str(per_type) + " 'choose' items, ids unique starting at 0.\n"
        + _QUALITY_BAR
    )


def _system_type_prompt(ttype: str, per_type: int) -> str:
    """Chunks 2..7 system prompt: one type per call."""
    return (
        "You are an expert English grammar exercise designer for Spanish-speaking "
        "learners (CEFR A1-B1). You will receive study material and must produce a "
        "JSON object consumed by a practice web app.\n\n"
        "RULES:\n"
        "- Output ONLY valid JSON. No markdown fences, no comments, no extra text.\n"
        '- The object has ONE key, "POOL": a list of EXACTLY ' + str(per_type) +
        ' items of type "' + ttype + '".\n\n'
        '"' + ttype + '" ITEM SHAPE:\n'
        + _TYPE_SHAPES[ttype] + "\n\n"
        "QUALITY BAR:\n"
        "- Exactly " + str(per_type) + " items, ids unique starting at 0.\n"
        "- Do NOT repeat exercise sentences already used in other blocks.\n"
        + _QUALITY_BAR
    )


def _system_single_prompt(per_type: int) -> str:
    """Single-call system prompt: the full CONFIG (small pools)."""
    shapes = "\n".join(_TYPE_SHAPES[t] for t in _EXERCISE_TYPES)
    return (
        "You are an expert English grammar exercise designer for Spanish-speaking "
        "learners (CEFR A1-B1). You will receive study material and must produce the "
        "configuration object that a practice web app renders.\n\n"
        "RULES:\n"
        "- Output ONLY valid JSON. No markdown fences, no comments, no extra text.\n"
        "- The JSON object has exactly 6 top-level keys:\n"
        '  1. "title": short thematic title with an emoji.\n'
        '  2. "subtitle": one English sentence describing the practice.\n'
        '  3. "tags": array of key vocabulary/grammar words from the material (max 15).\n'
        '  4. "SECTIONS": the 7 section descriptors below (num 1..7, same titles).\n'
        '  5. "POOL": the item pool covering every grammar point in the material.\n'
        '  6. "MEANINGS": map word -> short Spanish gloss for key vocabulary.\n\n'
        "SECTIONS (fixed):\n" + _SECTIONS_JSON_TEXT + "\n\n"
        "POOL ITEM SHAPES:\n" + shapes + "\n\n"
        "QUALITY BAR:\n"
        "- At least " + str(per_type) + " items PER TYPE inside POOL (" +
        str(per_type * 7) + "+ total).\n"
        + _QUALITY_BAR
    )


def _build_user_prompt(source_content: str) -> str:
    """Build the user message: the study material (truncated defensively)."""
    material = source_content.strip()
    if len(material) > _MAX_MATERIAL_CHARS:
        material = material[:_MAX_MATERIAL_CHARS] + "\n…[material truncated]"
    return (
        "Generate the JSON the system prompt requests, based on the study material "
        "below. If the material is long, focus on the most salient grammar patterns "
        "and reuse its vocabulary.\n\n"
        "MATERIAL\n========\n" + material
    )


def _split_slices(source: str, slice_chars: int, slice_cap: int) -> list[str]:
    """Line-greedy split of *source* into <= *slice_cap* slices of <= *slice_chars*.

    Pure (no I/O, no logging) for testability. Lines stay whole whenever
    possible; a single line longer than *slice_chars* is hard-cut and the
    remainder starts the next slice. Any content beyond the *slice_cap*-th
    slice is silently dropped (caller logs the discard).
    """
    if not source:
        return []
    lines = source.splitlines() or [source]
    slices: list[str] = []
    buf: list[str] = []
    cur = 0
    for line in lines:
        line_len = len(line)
        sep = 1 if buf else 0
        if buf and cur + sep + line_len <= slice_chars:
            buf.append(line)
            cur += sep + line_len
            continue
        # Commit current buffer if non-empty.
        if buf:
            slices.append("\n".join(buf))
            if len(slices) >= slice_cap:
                return slices
        # Single oversized line: hard-cut and seed next slice with the tail.
        if line_len > slice_chars:
            slices.append(line[:slice_chars])
            if len(slices) >= slice_cap:
                return slices
            remainder = line[slice_chars:]
            while len(remainder) > slice_chars:
                slices.append(remainder[:slice_chars])
                if len(slices) >= slice_cap:
                    return slices
                remainder = remainder[slice_chars:]
            if remainder:
                buf = [remainder]
                cur = len(remainder)
            else:
                buf, cur = [], 0
        else:
            buf = [line]
            cur = line_len
    if buf and len(slices) < slice_cap:
        slices.append("\n".join(buf))
    return slices


# ─── Template / JSON helpers ─────────────────────────────────────────

def _load_template() -> str:
    try:
        html = ASSET_TEMPLATE.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"No se pudo leer el template de ejercicios: {e}")
    if "var CONFIG=" not in html:
        raise RuntimeError("Template corrupt: falta el ancla 'var CONFIG='")
    return html


def _extract_json(text: str) -> dict | None:
    """Parse the model answer as JSON, tolerating ``` fences and stray text."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        t = t.strip()
    try:
        parsed = json.loads(t)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: slice the outermost {...} block.
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(t[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _extract_pool(text: str) -> list | None:
    """Extract the ``POOL`` list from a chunk answer (dict or bare array)."""
    parsed = _extract_json(text)
    if isinstance(parsed, dict):
        pool = parsed.get("POOL")
        return pool if isinstance(pool, list) else None
    if isinstance(parsed, list):
        return parsed
    return None


def _build_sections(types: list[str]) -> list[dict]:
    """Section descriptors (fixed titles) for the given present types."""
    return [
        {"type": t, "num": i + 1, "title": title, "sub": sub}
        for i, (t, title, sub) in enumerate(_SECTION_DEFS)
        if t in types
    ]


def _reindex_pool(pool: list[dict]) -> list[dict]:
    for i, item in enumerate(pool):
        item["id"] = i
    return pool


# ─── Helpers de normalización compartidos por sync y validadores ──────

_PUNCT_END = re.compile(r"[.,;:!?'\"]+$")


def _norm_token(w) -> str:
    """Token normalizado (lowercase sin puntuación final). Helper compartido."""
    return _PUNCT_END.sub("", str(w)).lower()


def _tokens_of(s: str) -> list[str]:
    """Whitespace-split del string ``s`` con tokens normalizados (lower + sin
    punt. final). Vacíos fuera. Helper compartido entre sync y validadores.
    """
    return [_norm_token(w) for w in re.split(r"\s+", s.strip()) if w]


def _check_order_tell_pieces(item: dict, iid) -> str | None:
    """Valida la consistencia ``tell`` ↔ ``pieces`` para ítems ``order`` sin
    ``a``. Exige que ``pieces`` sea exactamente el conjunto de palabras de
    ``tell`` (mismas palabras, lowercase, sin puntuación, 4..6 tokens; el
    orden se tolera — ``_sync_order_pieces`` lo canónica al orden de
    ``tell`` y emite ``a``).

    Devuelve ``None`` si pasa, o el mensaje de error en inglés (se reinyecta
    al LLM en el reintento) si falla. Si el ítem ya tiene ``a`` usable, el
    ``_sync_order_pieces`` previo ya forzó ``pieces`` al orden canónico y se
    omite el chequeo.
    """
    a = item.get("a")
    has_a = (isinstance(a, list) and bool(a)) or (isinstance(a, str) and a.strip())
    if has_a:
        return None
    tell = item.get("tell")
    pieces = item.get("pieces")
    if not isinstance(tell, str) or not tell.strip():
        return (f"item order {iid}: 'pieces' must contain exactly the words "
                f"of 'tell' (same words, lowercase, no punctuation, 4..6 "
                f"tokens)")
    ttoks = _tokens_of(tell)
    if not (4 <= len(ttoks) <= 6):
        return (f"item order {iid}: 'pieces' must contain exactly the words "
                f"of 'tell' (same words, lowercase, no punctuation, 4..6 "
                f"tokens)")
    ptoks = [_norm_token(p) for p in pieces] if isinstance(pieces, list) else []
    if sorted(ptoks) != sorted(ttoks):
        return (f"item order {iid}: 'pieces' must contain exactly the words "
                f"of 'tell' (same words, lowercase, no punctuation, 4..6 "
                f"tokens)")
    return None


def _sync_order_pieces(pool: list[dict]) -> list[dict]:
    """Haz los tokens de order/transform consistentes con su respuesta.

    Para order/transform la ÚNICA referencia de corrección es el campo 'a'
    (respuesta canónica). El LLM a veces genera 'pieces' (tokens) con typos o
    variantes que no coinciden con 'a' (p. ej. "invidet" vs "invited"), con lo
    que el usuario construye "tal cual" los tokens y la corrección le falla.
    Aquí forzamos pieces ≡ a cuando 'a' existe; si no, para 'order' derivamos
    el orden canónico de ``tell`` (4..6 palabras) cuando ``pieces`` es una
    permutación válida de las palabras normalizadas de ``tell``, emitiendo
    ``a`` como lista para que el template pueda corregir. En otro caso (o si
    ``tell`` no encaja) dejamos el ítem intacto: la validación posterior lo
    rechazará con un mensaje claro en el siguiente intento. El fallback
    genérico (derivar pieces de ``s``/``tell``) solo corre para transform sin
    ``a`` y sin ``pieces``. Además se limpia la puntuación final de cada
    token (p. ej. "me." → "me") para que el usuario no tenga que colocar el
    punto como token suelto. Mutates in place, devuelve el pool.
    """
    _ANS_TYPES = ("order", "transform")
    # ``_PUNCT_END`` está a nivel de módulo para reutilizarlo desde los
    # validadores.
    for item in pool:
        if item.get("type") not in _ANS_TYPES:
            continue
        a = item.get("a")
        canon = None
        if isinstance(a, list) and a:
            canon = [_PUNCT_END.sub("", str(w)) for w in a]
        elif isinstance(a, str) and a.strip():
            canon = [_PUNCT_END.sub("", w) for w in re.split(r"\s+", a.strip()) if w]
        if canon:
            canon = [w for w in canon if w]
            item["pieces"] = canon
        else:
            # ── ORDER-only: sin 'a' intentamos derivar el orden canónico de
            # ``tell``. Si ``pieces`` es una permutación válida de las
            # palabras normalizadas de ``tell`` (4..6 tokens) forzamos
            # ``pieces`` al orden de ``tell`` y emitimos ``a`` como lista
            # para que el template pueda corregir. Si no encaja, dejamos el
            # ítem intacto: la validación posterior lo rechazará.
            if item.get("type") == "order":
                tell = item.get("tell")
                if isinstance(tell, str) and tell.strip():
                    toks = [_PUNCT_END.sub("", w) for w in re.split(r"\s+", tell.strip()) if w]
                    pieces = item.get("pieces")
                    if (isinstance(pieces, list)
                            and 4 <= len(toks) <= 6
                            and sorted(_norm_token(w) for w in pieces)
                                == sorted(_norm_token(w) for w in toks)):
                        item["pieces"] = toks
                        item["a"] = toks
                # 'order' sin 'a' nunca cae al fallback genérico: si las
                # condiciones no encajan, dejamos el ítem intacto para que
                # la validación lo rechace (fail fast).
                continue
            pieces = item.get("pieces")
            if not pieces:
                # Sin a y sin pieces: derivamos de la base (nunca deja el ítem roto)
                base = item.get("s") or item.get("tell")
                if isinstance(base, str) and base.strip():
                    item["pieces"] = [w for w in re.split(r"\s+", base.strip()) if w]
    return pool


# ─── Validation ──────────────────────────────────────────────────────

def _validate_items(items, ttype: str, expected: int) -> str | None:
    """Validate one typed block; return an error message or None."""
    if not isinstance(items, list) or not items:
        return f"Bloque '{ttype}' vacío"
    if len(items) < expected:
        return f"Bloque '{ttype}': {len(items)} ítems, se requieren ≥ {expected}"
    seen: set = set()
    for item in items:
        if not isinstance(item, dict) or item.get("type") != ttype:
            return f"Ítem de tipo inválido en bloque '{ttype}'"
        iid = item.get("id")
        if iid in seen:
            return f"id duplicado en bloque '{ttype}': {iid!r}"
        seen.add(iid)
        # Shape-specific contracts (fail fast instead of rendering broken UI).
        if ttype in ("choose", "error"):
            opts = item.get("opts")
            if not isinstance(opts, list) or len(opts) != 4 or item.get("a") not in opts:
                return f"Ítem {ttype} {iid}: 'a' debe estar en opts[4]"
        elif ttype == "tf":
            if not isinstance(item.get("a"), bool):
                return f"Ítem tf {iid}: 'a' debe ser booleano"
        elif ttype in ("order", "transform"):
            pieces = item.get("pieces")
            if not isinstance(pieces, list) or not (4 <= len(pieces) <= 6):
                return f"Ítem {ttype} {iid}: 'pieces' debe tener 4..6 tokens"
            if ttype == "order":
                # Consistencia tell ↔ pieces (rechaza LLM que entrega 'pieces'
                # desordenadas o que no casan con 'tell'). El helper ya salta
                # el chequeo cuando 'a' es usable (sync forzó pieces ≡ tell).
                err = _check_order_tell_pieces(item, iid)
                if err:
                    return err
        if ttype == "transform":
            for extra in ("instruction", "base"):
                if not isinstance(item.get(extra), str) or not item[extra].strip():
                    return f"Ítem transform {iid}: falta {extra!r}"
        for key in ("tell", "explain"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                return f"Ítem {ttype} {iid}: falta {key!r}"
    return None


def _validate_meta(cfg: dict, per_type: int) -> str | None:
    """Validate chunk 1 metadata + choose pool; return error or None."""
    for key in ("title", "subtitle"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            return f"metadata '{key}' vacío"
    if not isinstance(cfg.get("tags"), list):
        return "metadata 'tags' debe ser un array"
    if not isinstance(cfg.get("MEANINGS"), dict):
        return "metadata 'MEANINGS' debe ser un objeto"
    return _validate_items(cfg.get("POOL"), "choose", per_type)


def _validate_config(cfg: dict, per_type: int,
                     required_types: tuple | list = _EXERCISE_TYPES) -> str | None:
    """Final CONFIG validation; return an error message or None."""
    if not isinstance(cfg, dict):
        return "CONFIG no es un objeto JSON"
    sections = cfg.get("SECTIONS")
    pool = cfg.get("POOL")
    if not isinstance(sections, list) or not sections:
        return "SECTIONS ausente o vacío"
    if not isinstance(pool, list) or not pool:
        return "POOL ausente o vacío"
    if len(sections) < 4:
        return "Se requieren al menos 4 secciones de ejercicio"

    per_type_seen: dict[str, int] = {}
    seen_ids: set = set()
    for item in pool:
        if not isinstance(item, dict) or item.get("type") not in _EXERCISE_TYPES:
            return f"Ítem con tipo inválido: {item!r}"
        itype = item["type"]
        per_type_seen[itype] = per_type_seen.get(itype, 0) + 1
        iid = item.get("id")
        if iid in seen_ids:
            return f"id duplicado en POOL: {iid!r}"
        seen_ids.add(iid)
        # Shape-specific contracts (shared with _validate_items).
        if itype in ("choose", "error"):
            opts = item.get("opts")
            if not isinstance(opts, list) or len(opts) != 4 or item.get("a") not in opts:
                return f"Ítem {itype} {iid}: 'a' debe estar en opts[4]"
        elif itype == "tf":
            if not isinstance(item.get("a"), bool):
                return f"Ítem tf {iid}: 'a' debe ser booleano"
        elif itype in ("order", "transform"):
            pieces = item.get("pieces")
            if not isinstance(pieces, list) or not (4 <= len(pieces) <= 6):
                return f"Ítem {itype} {iid}: 'pieces' debe tener 4..6 tokens"
            if itype == "order":
                # Consistencia tell ↔ pieces (idem _validate_items; post-sync
                # los healthy ya tienen 'a' y el helper los salta).
                err = _check_order_tell_pieces(item, iid)
                if err:
                    return err
        if itype == "transform":
            for extra in ("instruction", "base"):
                if not isinstance(item.get(extra), str) or not item[extra].strip():
                    return f"Ítem transform {iid}: falta {extra!r}"
        for key in ("tell", "explain"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                return f"Ítem {itype} {iid}: falta {key!r}"

    missing = [t for t in required_types if per_type_seen.get(t, 0) < per_type]
    if missing:
        return (
            f"POOL insuficiente: {len(pool)} ítems, faltan {per_type}+ por tipo "
            f"en: {', '.join(missing)}"
        )
    return None


def _inject_config(html: str, cfg: dict) -> str:
    """Replace the template's ``var CONFIG={...};`` placeholder with the config.

    The template's object literal is always closed by the FIRST ``};`` after
    ``var CONFIG=`` (inner items end with `},` / `]`; MEANINGS ends with `}`).
    """
    payload = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    marker = "var CONFIG="
    idx = html.find(marker)
    if idx == -1:
        raise RuntimeError("Template corrupt: falta 'var CONFIG='")
    end = html.find("};", idx + len(marker))
    if end == -1:
        raise RuntimeError("Template corrupt: no se encontró el cierre del CONFIG")
    end += len("};")
    return html[:idx] + "var CONFIG=" + payload + ";" + html[end:]


# ─── LLM chunk calls ─────────────────────────────────────────────────

def _run_llm_with_retry(system_prompt: str, user_prompt: str,
                        validator, chunk_label: str,
                        provider_name: str = "notebooklm"):
    """Call OpenZEN (fresh session per attempt), validating and retrying once.

    Args:
        validator: fn(raw_text) -> (parsed, reason) — reason None on success.

    Returns:
        The parsed value produced by the validator.

    Raises:
        RuntimeError: If every attempt fails validation or the answer is empty.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    reason = "respuesta vacía"
    for attempt in range(_MAX_CHUNK_RETRIES + 1):
        provider = get_provider(provider_name)  # fresh session per attempt
        result = provider.chat(messages=messages)
        raw = (result.get("content") or "").strip()
        if raw:
            parsed, reason = validator(raw)
            if reason is None:
                return parsed
        if attempt < _MAX_CHUNK_RETRIES:
            messages.append({
                "role": "user",
                "content": (
                    "Your previous answer was rejected because: " + str(reason) + ". "
                    "Output ONLY valid JSON again, fixing every reported problem."
                ),
            })
    raise RuntimeError(
        f"Bloque '{chunk_label}' falló tras {_MAX_CHUNK_RETRIES + 1} intentos: {reason}"
    )


# ─── Generation strategies ───────────────────────────────────────────

def _generate_single_call(task_id: str, user_prompt: str, per_type: int,
                         provider_name: str = "notebooklm") -> dict:
    """per_type <= 5: one call producing the full CONFIG."""
    _set_progress(task_id, "Generando ejercicios (todos los tipos)…")

    def _parse_single(raw: str):
        cfg = _extract_json(raw)
        if cfg is None:
            return None, "JSON inválido"
        reason = _validate_config(cfg, per_type)
        return cfg, reason

    cfg = _run_llm_with_retry(
        _system_single_prompt(per_type), user_prompt, _parse_single,
        "CONFIG completo", provider_name,
    )
    _sync_order_pieces(cfg["POOL"])
    _reindex_pool(cfg["POOL"])
    return cfg


def _generate_chunked(task_id: str, user_prompt: str, per_type: int,
                     provider_name: str = "notebooklm") -> tuple[dict, list[str]]:
    """per_type >= 10: 7 calls (metadata+choose, then one type each) + merge."""
    section_titles = {t: title for t, title, _ in _SECTION_DEFS}

    # ── Chunk 1: metadata + choose ────────────────────────────────
    _set_progress(task_id, "Generando bloque 1/7 — Choose…")

    def _parse_meta(raw: str):
        cfg = _extract_json(raw)
        if cfg is None:
            return None, "JSON inválido"
        reason = _validate_meta(cfg, per_type)
        return cfg, reason

    try:
        meta_cfg = _run_llm_with_retry(
            _system_meta_prompt(per_type), user_prompt, _parse_meta,
            "metadata + Choose", provider_name,
        )
    except RuntimeError as e:
        raise RuntimeError(f"Fallo el bloque inicial (metadata + Choose): {e}") from e

    pools: list[list[dict]] = [meta_cfg["POOL"]]
    skipped: list[str] = []

    # ── Chunks 2..7: one type per call ────────────────────────────
    for pos, ttype in enumerate(_EXERCISE_TYPES[1:], start=2):
        label = f"{section_titles.get(ttype, ttype)}"
        _set_progress(task_id, f"Generando bloque {pos}/7 — {label}…")

        def _parse_type(raw: str, tt: str = ttype, pt: int = per_type):
            pool = _extract_pool(raw)
            if pool is None:
                return None, "JSON inválido (falta POOL)"
            reason = _validate_items(pool, tt, pt)
            return pool, reason

        try:
            items = _run_llm_with_retry(
                _system_type_prompt(ttype, per_type), user_prompt, _parse_type,
                label, provider_name,
            )
            pools.append(items)
        except RuntimeError:
            # Skip this type block, keep the healthy ones (continue-without).
            skipped.append(ttype)

    present_types = [t for t in _EXERCISE_TYPES if t not in skipped]
    cfg = {
        "title": meta_cfg.get("title") or "English Grammar Practice",
        "subtitle": meta_cfg.get("subtitle")
        or "Practice with exercises generated from your material.",
        "tags": meta_cfg.get("tags") or [],
        "MEANINGS": meta_cfg.get("MEANINGS") or {},
        "POOL": _sync_order_pieces(_reindex_pool([item for pool in pools for item in pool])),
        "SECTIONS": _build_sections(present_types),
    }
    return cfg, skipped


# ─── Task lifecycle ──────────────────────────────────────────────────

def create_grammar_task(
    topic_id: str, source_id: str, source_type: str, user_id: str,
    per_type: int = _DEFAULT_PER_TYPE,
    provider: str = "notebooklm",
) -> dict:
    """Synchronous task creator: validate block -> INSERT -> background thread.

    Args:
        topic_id: The topic for the exercises.
        source_id: The block id to use as source.
        source_type: 'markdown' | 'content' (PDF out of scope in v1 — S6).
        user_id: The user creating the task.
        per_type: Exercises per type (1, 5, 10, 20 or 40). Dictates chunking.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails or the source is not usable.
    """
    try:
        per_type = int(per_type)
    except (TypeError, ValueError):
        per_type = _DEFAULT_PER_TYPE
    if per_type not in _VALID_PER_TYPE:
        raise ValueError(
            f"per_type debe ser uno de {_VALID_PER_TYPE} (got {per_type!r})"
        )
    if not topic_id or not source_id or not source_type:
        raise ValueError("Missing required fields: topic_id, source_id, source_type")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(
            f"English exercises v1 solo soportan bloques markdown/content (got '{source_type}')"
        )
    if provider not in _GRAMMAR_PROVIDERS:
        raise ValueError(
            f"Invalid provider: '{provider}'. "
            f"Available: {', '.join(sorted(_GRAMMAR_PROVIDERS))}"
        )

    source_block = db.query_one(
        "SELECT * FROM blocks WHERE id = %s AND user_id = %s",
        (source_id, user_id),
    )
    if not source_block:
        raise ValueError("Source block not found")

    source_content = source_block["content"] or ""
    if not source_content.strip():
        raise ValueError("Source content is empty")

    # v3's ai_tasks.id has NO default; without it this INSERT always 500s.
    task_row = db.execute_returning(
        """INSERT INTO ai_tasks (id, user_id, topic_id, task_type, source_type, source_id, model_used, status)
           VALUES (%s, %s, %s, 'generate_grammar', %s, %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id, source_type, source_id, provider),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_grammar_generation,
        args=(task_id, source_content, per_type, provider),
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id}


def _set_progress(task_id: str, label: str) -> None:
    db.execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (label, task_id),
    )


# ─── Phase 68 — NotebookLM material pre-condensation ─────────────────


def _condense_material_for_notebooklm(task_id: str, source_content: str) -> str:
    """Digest source into compact bullet notes via per-slice NotebookLM calls.

    Strategy:
      S2 — stripped len <= _NB_MATERIAL_BUDGET → return as-is (zero NB calls).
      Else greedy line-split into <= _NB_SLICE_CAP slices of <= _NB_SLICE_CHARS
      (leftover beyond cap is dropped and noted via _set_progress), condense
      each slice via a fresh NB session with per_slice_cap budget. Per-slice
      try/except RuntimeError → SKIP (S4); if ALL slices fail → raise.
      Joined digest is hard-truncated to _NB_HARD_CAP if it exceeds the budget.
    """
    stripped = source_content.strip()
    if len(stripped) <= _NB_MATERIAL_BUDGET:
        # S2 fast-path: zero NB calls.
        _set_progress(task_id, "Material dentro del presupuesto, sin condensar…")
        return stripped

    slices = _split_slices(stripped, _NB_SLICE_CHARS, _NB_SLICE_CAP)
    n = len(slices)
    if n == 0:
        return stripped  # degenerate: behave like S2
    covered = sum(len(s) for s in slices)
    dropped = max(0, len(stripped) - covered)
    per_slice_cap = max(300, _NB_MATERIAL_BUDGET // n)
    # Math sanity: n * per_slice_cap <= n * (_NB_MATERIAL_BUDGET // n) <= _NB_MATERIAL_BUDGET.

    system_prompt = (
        "You are a study-material condenser for English grammar practice. "
        "You receive a slice of source material and must produce COMPACT bullet "
        "notes (rules, formulas, 2-3 example sentences) covering the slice's "
        "grammar/vocabulary. Output ONLY the notes — no preamble, no markdown "
        "fences, no commentary."
    )
    instruction = (
        f"Extract compact bullet notes (rules, formulas, 2-3 example sentences) "
        f"in <= {per_slice_cap} chars. Output only notes."
    )
    slice_tolerance = int(per_slice_cap * 1.4) + 80

    digest_parts: list[str] = []
    failures = 0
    for i, slice_text in enumerate(slices, start=1):
        _set_progress(task_id, f"Condensando material {i}/{n}…")

        def _validator(raw: str):
            text = raw.strip()
            if not text:
                return None, "respuesta vacía"
            if len(text) > slice_tolerance:
                return None, f"digest demasiado largo ({len(text)}>{slice_tolerance})"
            return text, None

        user_msg = instruction + "\n\nSLICE\n=====\n" + slice_text
        try:
            part = _run_llm_with_retry(
                system_prompt, user_msg, _validator,
                f"condense material {i}/{n}", "notebooklm",
            )
            digest_parts.append(part)
        except RuntimeError as e:
            failures += 1
            _set_progress(task_id, f"Slice {i}/{n} omitido: {e}")

    if dropped:
        _set_progress(task_id, f"Material descartado fuera de {n} slices: {dropped} chars")
    if failures == n:
        raise RuntimeError("NotebookLM no pudo condensar el material")

    digest = "\n\n".join(digest_parts)
    if len(digest) > _NB_MATERIAL_BUDGET:
        digest = digest[:_NB_HARD_CAP] + "…[digest truncated]"
        _set_progress(
            task_id,
            f"Digest excede {_NB_MATERIAL_BUDGET} chars, truncado a {_NB_HARD_CAP}",
        )
    return digest


def _run_grammar_generation(task_id: str, source_content: str,
                            per_type: int = _DEFAULT_PER_TYPE,
                            provider_name: str = "notebooklm") -> None:
    """Background thread: chunked OpenZEN prompts, validate + inject, store."""
    try:
        db.execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _set_progress(task_id, "Analizando material…")

        template = _load_template()

        # Phase 68 (#259) — pre-condense oversized material for NotebookLM only.
        # OpenZEN path is untouched (no behavior change).
        orig_len = len(source_content)
        if provider_name == "notebooklm":
            source_content = _condense_material_for_notebooklm(task_id, source_content)
        condensed_len = len(source_content)
        user_prompt = _build_user_prompt(source_content)

        # NotebookLM rejects oversized single-shot CONFIG prompts — Google status 3;
        # chunked = fresh session per chunk/retry (no history accumulation).
        use_single = provider_name != "notebooklm" and per_type <= _MULTI_CALL_THRESHOLD
        if use_single:
            cfg = _generate_single_call(task_id, user_prompt, per_type, provider_name)
            skipped: list[str] = []
            required_types = _EXERCISE_TYPES
        else:
            cfg, skipped = _generate_chunked(task_id, user_prompt, per_type, provider_name)
            required_types = tuple(s["type"] for s in cfg["SECTIONS"])

        error = _validate_config(cfg, per_type, required_types=required_types)
        if error:
            raise RuntimeError(f"CONFIG inválido: {error}")

        # El selector del template se limita al nivel pedido (implícito).
        cfg["sizeOptions"] = _SIZE_OPTIONS.get(per_type, [1, 5, 10, 20, 40])

        html = _inject_config(template, cfg)
        depth = _PROV_DEPTH.get(per_type, f"{per_type} por apartado")
        sel = "·".join(str(x) for x in cfg.get("sizeOptions") or [])
        prov = provenance_banner("english-practice.html",
                                 _GRAMMAR_LABEL.get(provider_name, provider_name),
                                 f"{depth} ({per_type} por apartado, selector {sel})" if sel else depth,
                                 counts=f"{len(cfg.get('POOL') or [])} ítems",
                                 skipped=skipped)
        html = inject_provenance(html, prov)

        sections = cfg.get("SECTIONS") or []
        pool = cfg.get("POOL") or []
        stats = {
            "sections": len(sections),
            "pool_items": len(pool),
            "per_type": per_type,
            "chunks": "single" if use_single
            else f"{len(_EXERCISE_TYPES)} ({len(_EXERCISE_TYPES) - len(skipped)} ok)",
            "skipped_types": skipped,
            "tags": len(cfg.get("tags") or []),
            "model": _GRAMMAR_LABEL.get(provider_name, provider_name),
            "pipeline": "grammar-exercises + openzen (chunked merge)",
            "material_chars": {"original": orig_len, "condensed": condensed_len},
        }
        done_msg = (
            f"✅ English exercises generados · {len(sections)} secciones · "
            f"{len(pool)} ítems ({per_type}/tipo)"
        )
        if skipped:
            done_msg += " ⚠️ omitidos: " + ", ".join(skipped)

        # Anti-zombie: si la tarea fue cancelada (p. ej. cuelgue del free tier)
        # mientras el hilo esperaba a opencode, no pisar el estado final.
        row = db.query_one("SELECT status FROM ai_tasks WHERE id = %s", (task_id,))
        if row and row["status"] != "processing":
            return

        db.execute(
            """UPDATE ai_tasks
               SET status = 'done', result_content = %s, coverage_data = %s,
                   error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (html, json.dumps(stats, ensure_ascii=False), done_msg, task_id),
        )

    except Exception as e:
        row = db.query_one("SELECT status FROM ai_tasks WHERE id = %s", (task_id,))
        if row and row["status"] != "processing":
            return  # tarea cancelada: el hilo zombie no debe re-marcarla
        db.execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )