"""
Knowledge Pipeline — topic text → provider structured markdown (Phase 38).
Dynamic chunking: plan pass → variable chunks → merge.

Ported to v3 (flat-import layout): `import database as db`,
`get_provider` from ``ai.providers``, templates from
``ai.notebooklm.md_templates_catalog`` / ``ai.notebooklm.md_templates``.
"""

import json
import logging
import re
import threading
import time

import database as db
from ai.providers import get_provider
from ai.notebooklm.md_templates_catalog import MD_TEMPLATES
from ai.notebooklm.md_templates import get_md_template_role, _length_instruction
from ai.notebooklm.utils import _new_task_id

logger = logging.getLogger(__name__)

# Plan pass retries — a degraded/stale opencode server can return empty
# or failed responses; retry with a FRESH session per attempt (same fix
# as youtube_zen fresh-session-per-chunk, commit f510a7b).
_PLAN_MAX_ATTEMPTS = 3
_PLAN_RETRY_DELAY_S = 2

# Phase 63 (Issue #254) — provider parametric. Allowlist mirrors
# backend/routes/ai.py — keep both in sync if extended.
_KP_PROVIDERS = {"notebooklm"}
# Label helpers for the coverage_data stats dict (no new DB columns;
# existing `model` + `pipeline` fields carry the dynamic value).
_PROVIDER_MODEL_LABEL = {
    "opencode-acp": "big-pickle via opencode-acp",
    "notebooklm": "notebooklm (gemini)",
}
_PROVIDER_PIPELINE_LABEL = {
    "opencode-acp": "openzen",
    "notebooklm": "notebooklm",
}

# ═══════════════════════════════════════════════════════════════════
# Depth-aware system prompts
# ═══════════════════════════════════════════════════════════════════
_PROMPT_CONCISE = (
    "You are an expert educational content creator. "
    "Generate a CONCISE but COMPLETE summary of the topic (150-300 words).\n\n"
    "Structure:\n"
    "- **Opening sentence**: What this topic is, in one clear line\n"
    "- **Core concepts**: The 3-5 most important ideas, each explained in 1-2 sentences\n"
    "- **Key distinction**: What makes this different from similar concepts\n"
    "- **Why it matters**: One sentence on practical relevance\n"
    "- **Quick reference**: A mini-table or bullet list of the essentials\n\n"
    "Rules:\n"
    "- Use precise, academic language — no filler, no fluff\n"
    "- Bold key terms on first use\n"
    "- Every sentence must teach something concrete\n"
    "- Reply ONLY with the markdown content\n"
    "- NO single H1 wrapping the whole note, NO introductions, NO farewells\n"
)

_PROMPT_STANDARD = (
    "You are an expert educational content creator. "
    "Generate a STANDARD educational note (500-1000 words) that teaches the topic clearly "
    "and thoroughly enough for someone to understand and apply it.\n\n"
    "Required sections:\n"
    "1. **Definition** — What it is, in 3-4 sentences with precise terminology and context\n"
    "2. **Key Concepts** — Break down into 4-6 core components, each explained with examples\n"
    "3. **How It Works** — Step-by-step or flow explanation with a diagram if applicable\n"
    "4. **Practical Examples** — At least 3 examples with increasing complexity:\n"
    "   - Basic usage (simplest case)\n"
    "   - Real-world scenario (common use case)\n"
    "   - Advanced pattern (power user)\n"
    "   Each example: code/command + explanation + expected output\n"
    "5. **Common Pitfalls** — 3-4 mistakes beginners make, with symptoms and fixes\n"
    "6. **Key Takeaways** — Bullet list of the 4-6 most important points to remember\n\n"
    "Rules:\n"
    "- Use the rich Obsidian format specified at the end: emoji ## section headings, colored callouts, **bold** for key terms\n"
    "- Include code blocks with language tags where relevant (```python, ```bash, etc.)\n"
    "- Use tables for comparisons when there are 3+ items to compare\n"
    "- Every concept MUST have at least one concrete example\n"
    "- Reply ONLY with the markdown content\n"
    "- NO single H1 wrapping the whole note, NO introductions, NO farewells\n"
)

# ─── Detailed Mode: Plan + Dynamic Chunks ────────────────────────

_PROMPT_PLAN = (
    "You are a senior university professor planning comprehensive course material.\n\n"
    "Given the topic below, generate a DETAILED OUTLINE for a thorough course note.\n"
    "The outline must have 8-15 sections (### headings), organized logically.\n\n"
    "IMPORTANT: Reply with ONLY the numbered outline. Each line must be:\n"
    "N. **Section Title** — brief description (target N words)\n\n"
    "Example format:\n"
    "1. **Definition & Context** — Precise definition, history, ecosystem (400 words)\n"
    "2. **Core Principles** — 5 fundamental principles with explanations (500 words)\n"
    "3. **Architecture** — Internal mechanisms, data flow, components (400 words)\n"
    "... and so on\n\n"
    "Guidelines for the outline:\n"
    "- Start with Definition/Overview\n"
    "- Include Principles/Fundamentals early\n"
    "- Architecture/Mechanism in the middle\n"
    "- Examples/Practice after theory\n"
    "- Configuration/Best Practices near the end\n"
    "- End with Takeaways and References\n"
    "- Each section target: 300-500 words (total will be 3000-6000 words)\n"
    "- Aim for 10-12 sections for a complex topic, 8-9 for simpler ones\n\n"
    "Reply with ONLY the numbered outline. No other text.\n"
)

_PROMPT_CHUNK_TEMPLATE = (
    "You are a senior university professor creating comprehensive, authoritative course material.\n"
    "Generate sections {start}-{end} of a DETAILED course note.\n"
    "This is chunk {chunk_num} of {total_chunks}.\n\n"
    "The full outline is:\n"
    "{outline}\n\n"
    "Generate ONLY sections {start}-{end} listed above.\n\n"
    "Requirements for EACH section:\n"
    "- Minimum 300-500 words per section\n"
    "- Use the rich Obsidian format specified at the end (emoji ## section headings, colored callouts, **bold** key terms)\n"
    "- Include code blocks with language tags and inline comments\n"
    "- Use tables for comparisons (minimum 3 items)\n"
    "- Use blockquotes > for important notes, warnings, tips\n"
    "- Every technical claim MUST be supported by example, explanation, or reference\n"
    "- Every recommendation MUST include rationale\n"
    "- Write as if teaching a student who wants DEEP mastery\n"
    "- university course textbook quality\n\n"
    "Rules:\n"
    "- Generate ONLY the sections specified — do NOT skip ahead or add extra sections\n"
    "- Do NOT repeat content from other chunks\n"
    "- NEVER repeat the same word or phrase multiple times in a row — if you run out of things to say, STOP\n"
    "- Reply ONLY with the markdown content\n"
    "- NO single H1 wrapping the whole note, NO introductions, NO farewells\n"
)

# ═══════════════════════════════════════════════════════════════════
# Rich Obsidian output format — appended LAST to every generation prompt
# so it is the most recent formatting instruction (overrides heading
# rules inside the base prompts). Produces colored callouts, bold key
# terms and emoji section headings, matching the course block style.
# (Identical to youtube_zen._FORMAT_RICH — keep in sync.)
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
    "- Bold (`**term**`) every important term on FIRST use.\n"
    "- Use markdown tables for comparisons of 3+ items "
    "(e.g. `| Expression | Meaning |`).\n"
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
# (Identical to youtube_zen._FORMAT_RICH_POR_TEMA — keep in sync.)
# ═══════════════════════════════════════════════════════════════════
_FORMAT_RICH_POR_TEMA = (
    "OUTPUT FORMAT — RICH OBSIDIAN MARKDOWN, ONE SECTION PER HEADING (MANDATORY):\n"
    "- Generate exactly ONE heading per section listed in the outline/plan "
    "above. Use plain `## Section Title` (or `###`) — do NOT split each "
    "section into many emoji sub-headings like `## 📖 Definición`.\n"
    "- Inside EACH section, use colored Obsidian callouts for the key "
    "content:\n"
    "  - `> [!info]` — blue facts\n"
    "  - `> [!warning]` — red/orange pitfalls or mistakes\n"
    "  - `> [!tip]` — green advice\n"
    "  - `> [!example]` — purple examples\n"
    "  Each callout: 2-4 sentences, first line a short bold label.\n"
    "- Bold (`**term**`) every important term on FIRST use.\n"
    "- Use markdown tables for comparisons of 3+ items "
    "(e.g. `| Expression | Meaning |`).\n"
    "- Use fenced code blocks (```lang) ONLY if the content mentions code.\n"
    "- NEVER wrap the whole note in a single H1 title; do NOT add "
    "introductions or farewells.\n"
    "- CRITICAL: keep ONE section per heading — group related concepts "
    "under the section heading with callouts instead of creating new "
    "headings.\n"
    "- This format REPLACES the heading rules in the prompt above.\n"
)

# ─── Language instruction suffixes ────────────────────────────────
# NOTE: These are prepended BEFORE the task prompt so the model
# sees the language rule first — this has more impact than appending it.
_LANG_INSTRUCTIONS = {
    "es": "¡IMPORTANTE! Debes responder EXCLUSIVAMENTE en español. "
          "No uses inglés bajo ningún concepto. Todo el contenido debe estar en español.",
    "en": "IMPORTANT! You MUST respond EXCLUSIVELY in English. "
          "Never use Spanish or any other language. All content must be in English.",
}

# Max sections per chunk (2 per chunk — smaller = fewer repetition loops)
_CHUNK_SIZE = 2


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


def _clean_callout_titles(text: str) -> str:
    """Strip bold markers from callout title lines:
    `> [!info] **Label**` → `> [!info] Label`.
    Only touches the callout's first line (the title); bold **term**
    markers inside the callout body are left untouched.
    """
    if not text:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*>+\s*\[!\w+\]\s*)\*\*(.+?)\*\*(.*)$', line)
        if m:
            lines[i] = m.group(1) + m.group(2) + m.group(3)
    return "\n".join(lines)


def _set_progress(
    task_id: str,
    step_idx: int,
    detail: str | None = None,
    steps: list | None = None,
) -> None:
    """Update progress steps in error_message column (stream modal reads it)."""
    step_list = steps or []
    if not step_list:
        return
    lines = []
    for i, (_key, label) in enumerate(step_list):
        if i < step_idx:
            lines.append(f"✅ {label}")
        elif i == step_idx:
            msg = f"⏳ {label}"
            if detail:
                msg += f" {detail}"
            lines.append(msg)
        else:
            lines.append(f"⬜ {label}")
    db.execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        ("\n".join(lines), task_id),
    )


def _check_cancelled(task_id: str) -> bool:
    """Check if user cancelled the task."""
    row = db.query_one(
        "SELECT status FROM ai_tasks WHERE id = %s",
        (task_id,),
    )
    return row is not None and row["status"] == "cancelled"


def _parse_outline(outline_text: str) -> list[dict]:
    """Parse the plan output into a list of sections.

    Returns list of {"num": int, "title": str, "desc": str, "words": int}.
    """
    sections = []
    # Match lines like: "1. **Title** — description (N words)"
    pattern = re.compile(
        r"(\d+)\.\s*\*\*(.+?)\*\*\s*[—–-]\s*(.+?)(?:\s*\((\d+)\s*words?\))?\s*$",
        re.MULTILINE,
    )
    for match in pattern.finditer(outline_text):
        num = int(match.group(1))
        title = match.group(2).strip()
        desc = match.group(3).strip()
        words = int(match.group(4)) if match.group(4) else 400
        sections.append({"num": num, "title": title, "desc": desc, "words": words})

    # Fallback: if regex didn't match, try simpler "N. Title" pattern
    if not sections:
        simple_pattern = re.compile(r"(\d+)\.\s*\*\*(.+?)\*\*", re.MULTILINE)
        for match in simple_pattern.finditer(outline_text):
            num = int(match.group(1))
            title = match.group(2).strip()
            sections.append({"num": num, "title": title, "desc": "", "words": 400})

    return sections


def _build_dynamic_steps(plan_text: str) -> list[tuple[str, str]]:
    """Build progress steps dynamically based on the plan."""
    sections = _parse_outline(plan_text)
    num_chunks = max(1, (len(sections) + _CHUNK_SIZE - 1) // _CHUNK_SIZE)

    steps = [("validate", "📝 Validando entrada...")]
    steps.append(("plan", f"📋 Generando plan ({len(sections)} secciones estimadas)..."))
    for i in range(num_chunks):
        chunk_start = i * _CHUNK_SIZE + 1
        chunk_end = min((i + 1) * _CHUNK_SIZE, len(sections))
        steps.append((
            f"chunk_{i+1}",
            f"🤖 Generando chunk {i+1}/{num_chunks} (secciones {chunk_start}-{chunk_end})...",
        ))
    steps.append(("merge", "🔗 Combinando resultados..."))
    return steps


def create_knowledge_pipeline_task(
    topic_text: str,
    depth: str,
    topic_id: str | None,
    user_id: str,
    course_id: str | None = None,
    language: str = "es",
    mode: str = "unitema",
    template_id: str | None = None,
    provider: str = "notebooklm",
) -> dict:
    """Synchronous task creator: topic text → provider-specific structured markdown.

    Args:
        topic_text: Free-text topic description from the user.
        depth: One of 'concise', 'standard', 'detailed'.
        topic_id: Optional topic to associate the result with.
        user_id: The user creating the task.
        course_id: Optional course for block insertion on completion.
        language: 'es' for Spanish, 'en' for English.
        mode: 'unitema' (single block) or 'por_tema' (section → separate topic).
        template_id: Optional MD_TEMPLATES key; None → generic prompt.
        provider: 'opencode-acp' (default, OpenZEN / big-pickle) or 'notebooklm'.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails.
    """
    if not topic_text or not topic_text.strip():
        raise ValueError("Missing required field: topic_text")

    depth = (depth or "standard").strip().lower()
    if depth not in ("concise", "standard", "detailed"):
        raise ValueError(
            f"Invalid depth: '{depth}'. Must be one of: concise, standard, detailed"
        )

    language = (language or "es").strip().lower()
    if language not in _LANG_INSTRUCTIONS:
        raise ValueError(
            f"Invalid language: '{language}'. Must be one of: es, en"
        )

    mode = (mode or "unitema").strip().lower()
    if mode not in ("unitema", "por_tema"):
        mode = "unitema"

    template_id_val = (template_id or "").strip() or None
    if template_id_val is not None and template_id_val not in MD_TEMPLATES:
        raise ValueError(f"Plantilla no válida: {template_id_val}")

    provider_val = (provider or "notebooklm").strip()
    if provider_val not in _KP_PROVIDERS:
        raise ValueError(
            f"Invalid provider: '{provider_val}'. Only 'notebooklm' is available in v3"
        )

    topic_id_val = topic_id or ""
    course_id_val = course_id or ""

    # v3's ai_tasks.id has NO default; without it this INSERT always 500s.
    task_row = db.execute_returning(
        """INSERT INTO ai_tasks
               (id, user_id, topic_id, task_type, source_type, source_id, status, template_id)
            VALUES (%s, %s, %s, 'knowledge_pipeline', 'topic', %s, 'pending', %s)
            RETURNING id""",
        (_new_task_id(), user_id, topic_id_val, course_id_val, template_id_val),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_knowledge_pipeline,
        args=(task_id, topic_text.strip(), depth, language, mode, template_id_val, provider_val),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


def _run_knowledge_pipeline(
    task_id: str,
    topic_text: str,
    depth: str,
    language: str = "es",
    mode: str = "unitema",
    template_id: str | None = None,
    provider_name: str = "notebooklm",
) -> None:
    """Background thread: topic text → provider-driven structured markdown."""
    try:
        db.execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        if _check_cancelled(task_id):
            return
        lang_instruction = _LANG_INSTRUCTIONS.get(language, _LANG_INSTRUCTIONS["es"])
        if depth == "detailed":
            _run_detailed_dynamic(task_id, topic_text, lang_instruction, provider_name, mode, template_id)
        else:
            _run_single_pass(task_id, topic_text, depth, lang_instruction, provider_name, mode, template_id)
    except Exception as e:
        if _check_cancelled(task_id):
            db.execute(
                "UPDATE ai_tasks SET status='cancelled', error_message='Cancelado por el usuario',"
                " completed_at=NOW(), updated_at=NOW() WHERE id=%s", (task_id,),
            )
        else:
            db.execute(
                "UPDATE ai_tasks SET status='error', error_message=%s,"
                " completed_at=NOW(), updated_at=NOW() WHERE id=%s", (str(e), task_id),
            )


def _run_single_pass(
    task_id: str, topic_text: str, depth: str, lang_instruction: str, provider_name: str = "notebooklm",
    mode: str = "unitema",
    template_id: str | None = None,
) -> None:
    """Single provider call for concise/standard depth."""
    provider = get_provider(provider_name)
    plabel = _PROVIDER_PIPELINE_LABEL.get(provider_name, provider_name)
    _set_progress(task_id, 0, steps=[
        ("validate", "📝 Validando entrada..."),
        ("generate", f"🤖 Generando notas con {plabel}..."),
    ])
    if _check_cancelled(task_id):
        return

    role = get_md_template_role(template_id)
    if role:
        base_prompt = role
    else:
        base_prompt = _PROMPT_CONCISE if depth == "concise" else _PROMPT_STANDARD
    # Language instruction FIRST, then task prompt, then the depth length
    # hint, then the rich Obsidian output format (most recent formatting
    # rule). por_tema uses the bounded variant so the frontend split stays
    # at one block per section.
    length_hint = _length_instruction(depth)
    format_rule = _FORMAT_RICH_POR_TEMA if mode == "por_tema" else _FORMAT_RICH
    system_prompt = f"{lang_instruction}\n\n{base_prompt}\n\n{length_hint}{format_rule}"
    user_message = (
        f"Generate educational notes about the following topic "
        f"(depth: {depth}):\n\n"
        f"{topic_text}"
    )
    result = provider.chat(messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ])
    content = result.get("content", "").strip()
    if not content:
        raise RuntimeError(f"{plabel} devolvió una respuesta vacía")
    if _check_cancelled(task_id):
        return
    content = _clean_callout_titles(content)
    _set_progress(task_id, 1, steps=[
        ("validate", "📝 Validando entrada..."),
        ("generate", f"🤖 Generando notas con {plabel}..."),
    ])
    stats = {
        "title": topic_text.strip().splitlines()[0].strip() if topic_text.strip() else topic_text,
        "input_chars": len(topic_text), "output_chars": len(content),
        "depth": depth, "language": lang_instruction.split()[-1][:2],
        "parts": 1,
        "model": _PROVIDER_MODEL_LABEL.get(provider_name, provider_name),
        "pipeline": f"knowledge-pipeline + {_PROVIDER_PIPELINE_LABEL.get(provider_name, provider_name)}",
        "mode": mode, "template_id": template_id,
    }
    done_msg = (
        f"✅ Generador Contenido completado ({depth}) · "
        f"{len(topic_text)} chars input → {len(content)} chars output"
    )
    db.execute(
        "UPDATE ai_tasks SET status='done', result_content=%s, coverage_data=%s,"
        " error_message=%s, completed_at=NOW(), updated_at=NOW() WHERE id=%s",
        (content, json.dumps(stats), done_msg, task_id),
    )


def _run_detailed_dynamic(
    task_id: str,
    topic_text: str,
    lang_instruction: str,
    provider_name: str = "notebooklm",
    mode: str = "unitema",
    template_id: str | None = None,
) -> None:
    """Detailed mode: plan → dynamic chunks → merge."""
    provider = get_provider(provider_name)
    plabel = _PROVIDER_PIPELINE_LABEL.get(provider_name, provider_name)
    # ── Step 0: Validate ──
    _set_progress(task_id, 0, steps=[
        ("validate", "📝 Validando entrada..."),
        ("plan", "📋 Generando plan..."),
    ])

    if _check_cancelled(task_id):
        return

    # ── Step 1: Plan pass ──
    _set_progress(task_id, 1, steps=[
        ("validate", "📝 Validando entrada..."),
        ("plan", "📋 Generando plan de secciones..."),
    ])

    # Put lang_instruction FIRST so the model registers it before the task
    plan_system = f"{lang_instruction}\n\n{_PROMPT_PLAN}"
    plan_user = (
        f"Generate a detailed outline for a comprehensive course note about:\n\n"
        f"{topic_text}"
    )
    plan_messages = [
        {"role": "system", "content": plan_system},
        {"role": "user", "content": plan_user},
    ]

    # Retry loop with a FRESH provider/session per attempt — a degraded
    # opencode server or stale session can return empty responses, and a
    # single transient failure must not kill the whole detailed task.
    plan_text = ""
    last_plan_error: Exception | None = None
    for attempt in range(1, _PLAN_MAX_ATTEMPTS + 1):
        if _check_cancelled(task_id):
            return
        try:
            plan_result = provider.chat(messages=plan_messages)
            plan_text = (plan_result.get("content") or "").strip()
            if plan_text:
                break
            last_plan_error = RuntimeError("respuesta sin contenido")
            logger.warning(
                "[knowledge_pipeline] Plan attempt %d/%d: empty response",
                attempt, _PLAN_MAX_ATTEMPTS,
            )
        except Exception as e:  # noqa: BLE001 — retry any provider failure
            last_plan_error = e
            logger.warning(
                "[knowledge_pipeline] Plan attempt %d/%d failed: %s",
                attempt, _PLAN_MAX_ATTEMPTS, e,
            )
        if attempt < _PLAN_MAX_ATTEMPTS:
            _set_progress(task_id, 1, steps=[
                ("validate", "📝 Validando entrada..."),
                ("plan", f"📋 Reintentando plan ({attempt + 1}/{_PLAN_MAX_ATTEMPTS})..."),
            ])
            time.sleep(_PLAN_RETRY_DELAY_S)

    if not plan_text:
        detail = f" — último error: {last_plan_error}" if last_plan_error else ""
        raise RuntimeError(
            f"{plabel} devolvió un plan vacío tras {_PLAN_MAX_ATTEMPTS} intentos{detail}"
        )

    if _check_cancelled(task_id):
        return

    # ── Parse plan and build dynamic steps ──
    sections = _parse_outline(plan_text)
    if not sections:
        # Fallback: treat entire plan as single chunk
        sections = [{"num": 1, "title": "Full Topic", "desc": topic_text, "words": 400}]

    num_chunks = max(1, (len(sections) + _CHUNK_SIZE - 1) // _CHUNK_SIZE)
    dynamic_steps = _build_dynamic_steps(plan_text)

    # ── Step 2-N: Generate chunks ──
    parts: list[str] = []

    for chunk_idx in range(num_chunks):
        if _check_cancelled(task_id):
            return

        chunk_start = chunk_idx * _CHUNK_SIZE
        chunk_end = min(chunk_start + _CHUNK_SIZE, len(sections))
        chunk_sections = sections[chunk_start:chunk_end]

        # Update progress
        _set_progress(task_id, 2 + chunk_idx, steps=dynamic_steps)

        # Build section list for the prompt
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

        # Language instruction FIRST, then task prompt (template role when
        # one is selected, else the chunk prompt), then the depth length
        # hint, then the rich Obsidian output format. por_tema uses the
        # bounded variant so the frontend split stays at one block per
        # section.
        role = get_md_template_role(template_id)
        length_hint = _length_instruction("detailed")
        format_rule = _FORMAT_RICH_POR_TEMA if mode == "por_tema" else _FORMAT_RICH
        task_prompt = role if role else chunk_prompt
        system_prompt = f"{lang_instruction}\n\n{task_prompt}\n\n{length_hint}{format_rule}"
        user_message = (
            f"Generate the following sections for a comprehensive course note about:\n\n"
            f"{topic_text}\n\n"
            f"Sections to generate:\n{section_list}"
        )

        result = provider.chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])

        content = result.get("content", "").strip()
        if not content:
            raise RuntimeError(
                f"{plabel} devolvió respuesta vacía en chunk {chunk_idx + 1}"
            )
        content = _sanitize_output(content)
        parts.append(content)

    if _check_cancelled(task_id):
        return

    # ── Final: Merge ──
    _set_progress(task_id, len(dynamic_steps) - 1, steps=dynamic_steps)
    merged_content = "\n\n---\n\n".join(parts)
    merged_content = _clean_callout_titles(merged_content)

    stats = {
        "title": topic_text.strip().splitlines()[0].strip() if topic_text.strip() else topic_text,
        "input_chars": len(topic_text),
        "output_chars": len(merged_content),
        "depth": "detailed",
        "language": lang_instruction.split()[-1][:2] if lang_instruction else "es",
        "parts": len(parts),
        "sections": len(sections),
        "model": _PROVIDER_MODEL_LABEL.get(provider_name, provider_name),
        "pipeline": f"knowledge-pipeline + {_PROVIDER_PIPELINE_LABEL.get(provider_name, provider_name)} (dynamic chunking)",
        "mode": mode, "template_id": template_id,
    }

    done_msg = (
        f"✅ Generador Contenido completado (detailed) · "
        f"{len(sections)} secciones → {len(parts)} chunks → "
        f"{len(merged_content)} chars output"
    )

    db.execute(
        """UPDATE ai_tasks
           SET status = 'done', result_content = %s, coverage_data = %s,
               error_message = %s,
               completed_at = NOW(), updated_at = NOW()
           WHERE id = %s""",
        (merged_content, json.dumps(stats), done_msg, task_id),
    )
