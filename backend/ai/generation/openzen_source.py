"""OpenZen (OpenCode) source → study notes, chunked per section.

Why this exists
---------------
NotebookLM's ``nb_pdf_to_markdown`` hands the whole document to the model in
ONE atomic call (``ai/notebooklm/client.py``). There is no seam to add detail
at, and no seam to retry: if the call fails the whole document is lost, and
if it succeeds with a stingy length the ceiling is the model's choice, not
ours. That is why "Extensa" could never actually deliver in NotebookLM.

Here the source — a PDF *or* a Markdown block — is the source of truth and it
is split into sections. Each section is generated on its own, so:

* a section that fails can be retried WITHOUT losing the others;
* a section that keeps failing can be skipped with a visible warning, and
  regenerated later on its own;
* verbosity is a per-section instruction, not a hope.

Markdown sources already carry their own structure, so splitting them means
reading the headings the author wrote rather than guessing.

State lives in ``ai_tasks.coverage_data`` (a JSON column that already exists)
and the assembled document carries ``<!--chunk:N-->`` markers, so a single
chunk can be re-generated and spliced back into place without a schema change.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from ai.notebooklm.utils import _coerce_id, _new_task_id
from ai.providers import get_provider
from ai.utils import _resolve_pdf_path
from database import execute, execute_returning, query_one

logger = logging.getLogger(__name__)

PROVIDER = "opencode-acp"
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (2, 5)
CHUNK_MARKER = "<!--chunk:{n}-->"

# Sections longer than this get split again, otherwise one dense chapter eats
# the whole context window and the tail of it silently degrades.
MAX_SECTION_CHARS = 6000
MIN_SECTION_CHARS = 400

_HEADING = re.compile(r"^\s*(#{1,6}\s+.+|\d+(?:\.\d+)*[.)]\s+\S.+|[A-ZÁÉÍÓÚÑ][^.!?\n]{3,70})\s*$")

# Block types, following ai/notebooklm/tasks_md.py: a PDF points at a file in
# `url`, a Markdown block carries its text in `content`.
PDF_TYPES = ("pdf", "pdf-ref")
MD_TYPES = ("markdown", "content")

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


# ─── Section extraction ─────────────────────────────────────────────
def _pdf_pages_text(path: str) -> list[str]:
    """Return the text of each page. The PDF is the source of truth."""
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    try:
        return [page.get_text("text") or "" for page in doc]
    finally:
        doc.close()


def _split_windows(text: str) -> list[str]:
    """Split over-long text on paragraph boundaries, never mid-sentence."""
    if len(text) <= MAX_SECTION_CHARS:
        return [text]
    paras = text.split("\n\n")
    out: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 > MAX_SECTION_CHARS:
            out.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        out.append(buf)
    # A single paragraph can still be oversized (a dense page with no blank
    # lines). Splitting on "\n\n" alone would hand the whole thing to one
    # call and blow the context window, so fall back to line/sentence/word
    # boundaries. Losing nothing is the point: the PDF is the source of truth.
    hard: list[str] = []
    for part in out:
        while len(part) > MAX_SECTION_CHARS:
            window = part[:MAX_SECTION_CHARS]
            cut = max(window.rfind(". "), window.rfind("\n"), window.rfind(" "))
            if cut <= 0:
                cut = MAX_SECTION_CHARS
            hard.append(part[:cut + 1].strip())
            part = part[cut + 1:]
        if part.strip():
            hard.append(part)
    return hard


def _sections_from_toc(pages: list[str], toc: list) -> list[dict]:
    """Group pages into sections using the PDF's own table of contents."""
    sections: list[dict] = []
    n_pages = len(pages)
    for idx, entry in enumerate(toc):
        title = str(entry[1]).strip() if len(entry) > 1 else f"Sección {idx + 1}"
        start = max(int(entry[2]) - 1, 0)
        end = int(toc[idx + 1][2]) - 1 if idx + 1 < len(toc) else n_pages
        if end <= start:
            continue
        text = "\n\n".join(p for p in pages[start:end] if p.strip())
        if text.strip():
            sections.append({"title": title, "text": text})
    return sections


def _sections_from_headings(text: str) -> list[dict]:
    """No TOC: treat heading-like lines as section starts."""
    lines = text.splitlines()
    marks: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            title = m.group(1).strip()
            # Skip one-liners that are really sentences, and short ALL-CAPS noise.
            if len(title) > 4 and not title.endswith((".", "!", "?")):
                marks.append((i, title))
    if len(marks) < 2:
        return []
    sections: list[dict] = []
    for i, (start, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        if len(body) >= MIN_SECTION_CHARS:
            sections.append({"title": title, "text": body})
    return sections


def _markdown_sections(md: str) -> list[dict]:
    """Split Markdown on the headings the author already wrote.

    A Markdown block states its own structure, so unlike a PDF there is no
    need to guess where a section begins. Text before the first heading is
    kept as a preamble section rather than dropped — it is source text too.
    """
    lines = md.splitlines()
    marks: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _MD_HEADING.match(line)
        if m:
            marks.append((i, m.group(2).strip()))

    if not marks:
        return [{"title": f"Parte {i + 1}", "text": t}
                for i, t in enumerate(_split_windows(md)) if t.strip()]

    sections: list[dict] = []
    if marks[0][0] > 0:
        preamble = "\n".join(lines[:marks[0][0]]).strip()
        if preamble:
            sections.append({"title": "Introducción", "text": preamble})
    for i, (start, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        if body:
            sections.append({"title": title, "text": body})
    if not sections:
        sections = [{"title": f"Parte {i + 1}", "text": t}
                    for i, t in enumerate(_split_windows(md)) if t.strip()]

    # Same rule as the PDF path: one long section must not become one huge
    # call. Without this a 40k-character Markdown block goes to the model in a
    # single request and the tail degrades silently.
    final: list[dict] = []
    for sec in sections:
        for part_i, chunk in enumerate(_split_windows(sec["text"])):
            title = sec["title"] if part_i == 0 else f"{sec['title']} (cont. {part_i + 1})"
            final.append({"title": title, "text": chunk})
    return final


def _extract_sections(path: str) -> list[dict]:
    """Split the PDF into sections: TOC first, then headings, then windows.

    Every branch returns real text from the PDF. Nothing is invented here —
    that is the generator's job and it is told not to do it.
    """
    pages = _pdf_pages_text(path)
    try:
        import fitz

        doc = fitz.open(path)
        try:
            toc = list(doc.get_toc() or [])
        finally:
            doc.close()
    except Exception:
        logger.warning("[openzen] no se pudo leer el TDC", exc_info=True)
        toc = []

    sections = _sections_from_toc(pages, toc) if toc else []
    if not sections:
        full = "\n\n".join(p for p in pages if p.strip())
        sections = _sections_from_headings(full)
    if not sections:
        sections = [{"title": f"Parte {i + 1}", "text": t}
                    for i, t in enumerate(_split_windows(
                        "\n\n".join(p for p in pages if p.strip())))]

    # Re-split anything still oversized so no single call blows the context.
    final: list[dict] = []
    for sec in sections:
        for part_i, chunk in enumerate(_split_windows(sec["text"])):
            title = sec["title"] if part_i == 0 else f"{sec['title']} (cont. {part_i + 1})"
            final.append({"title": title, "text": chunk})
    return final


def _sections_for(source_type: str, ref: str) -> list[dict]:
    """Resolve a source reference into sections, whatever its kind.

    ``ref`` is a file path for PDFs and the raw text for Markdown.
    """
    if source_type == "markdown":
        sections = _markdown_sections(ref or "")
    else:
        resolved = _resolve_pdf_path(ref)
        if not resolved:
            raise RuntimeError(f"PDF no encontrado: {ref}")
        sections = _extract_sections(resolved)
    if not sections:
        raise RuntimeError("no se pudo extraer texto del material de origen")
    return sections


# ─── Chunk state ────────────────────────────────────────────────────
def _load_state(task_id: str) -> dict:
    row = query_one("SELECT coverage_data FROM ai_tasks WHERE id = %s", (task_id,))
    if not row or not row.get("coverage_data"):
        return {"chunks": []}
    try:
        data = json.loads(row["coverage_data"])
    except (ValueError, TypeError):
        return {"chunks": []}
    return data if isinstance(data, dict) else {"chunks": []}


def _save_state(task_id: str, state: dict) -> None:
    execute(
        "UPDATE ai_tasks SET coverage_data = %s, updated_at = NOW() WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )


def _set_message(task_id: str, message: str) -> None:
    execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (message, task_id),
    )


# ─── Generation ─────────────────────────────────────────────────────
def _section_prompt(section: dict, num: int, total: int, title: str) -> str:
    """Ask for one section, grounded strictly in the text handed over.

    The source text MUST be inside the prompt. An instruction that merely
    says "use the material above" while omitting the material is worse than
    useless: the model has nothing to ground on and will write a fluent,
    plausible chapter that invents half of it. The PDF is the source of
    truth, so it travels with the request.
    """
    source = (section.get("text") or "").strip()
    return (
        f"You are writing section {num} of {total} of a set of study notes.\n"
        f"Section title: {title}\n\n"
        f"<source_material>\n{source}\n</source_material>\n\n"
        "RULES (non-negotiable):\n"
        "1. Use ONLY the source material above. Do not add facts, dates, "
        "numbers, definitions or examples that are not in it.\n"
        "2. If the material does not cover something, leave it out. Do not "
        "fill the gaps with general knowledge.\n"
        "3. Stay inside this section's scope; do not summarise other "
        "sections.\n"
        "4. Begin directly with a `##` heading carrying the section title. "
        "No preamble, no introduction, no closing remarks, no commentary "
        "about the source."
    )


def _system_message(language: str, role: str, length_hint: str) -> str:
    """Compose the system prompt from the three existing instruction blocks.

    ``language`` goes through ``_lang_instruction`` because 'auto' means
    "follow the source language" — passing the literal word 'auto' to the
    model produces "producing auto study notes", which is meaningless.
    """
    from ai.notebooklm.md_templates import _lang_instruction

    parts = [p.strip() for p in (role, _lang_instruction(language), length_hint) if p and p.strip()]
    return "\n".join(parts)


def _generate_section(provider, section: dict, num: int, total: int,
                      language: str, length_hint: str, role: str = "") -> str:
    """Call the provider once for one section. Caller handles retries."""
    result = provider.chat(messages=[
        {"role": "system", "content": _system_message(language, role, length_hint)},
        {"role": "user", "content": _section_prompt(section, num, total, section["title"])},
    ])
    content = (result.get("content") or "").strip()
    if not content:
        raise RuntimeError("el modelo devolvió una sección vacía")
    return content


def _run_with_retry(provider, section, num, total, language, length_hint,
                    role: str = "", on_attempt=None):
    """Retry one section in isolation.

    Raises the last error once the attempts are exhausted — the caller decides
    what that means (skip with a warning, here, so one bad section cannot cost
    the user the whole document).
    """
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _generate_section(provider, section, num, total, language,
                                     length_hint, role)
        except Exception as e:  # noqa: BLE001 — any provider failure is retryable
            last = e
            logger.warning("[openzen] sección %d intento %d/%d falló: %s",
                           num, attempt, MAX_ATTEMPTS, e)
            if on_attempt:
                on_attempt(attempt, str(e))
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])
    raise last if last else RuntimeError("fallo desconocido")


def _assemble(pieces: dict[int, str]) -> str:
    """Join generated sections in order, keeping their markers."""
    return "\n\n".join(
        f"{CHUNK_MARKER.format(n=n)}\n{pieces[n]}" for n in sorted(pieces) if pieces.get(n)
    )


_MARKER_RE = re.compile(r"<!--chunk:(\d+)-->")


def _insert_chunk(content: str, n: int, body: str) -> str:
    """Put chunk ``n`` back in its own position in the assembled document.

    A chunk that failed has no marker of its own, so a plain append would
    silently move it to the end of the notes — section 3 landing after
    section 9. When the marker is missing we splice it in before the first
    higher-numbered chunk that did make it, which restores document order.
    """
    marker = CHUNK_MARKER.format(n=n)
    start = content.find(marker)
    if start != -1:
        body_start = start + len(marker)
        nxt = content.find("\n\n<!--chunk:", body_start)
        end = nxt if nxt != -1 else len(content)
        return content[:body_start] + "\n" + body + content[end:]

    at = -1
    for other in _MARKER_RE.finditer(content):
        if int(other.group(1)) > n:
            at = other.start()
            break
    if at == -1:
        head = content.rstrip()
        return f"{head}\n\n{marker}\n{body}" if head else f"{marker}\n{body}"
    return f"{content[:at].rstrip()}\n\n{marker}\n{body}\n\n{content[at:]}"


def _run_openzen_md(task_id: str, source_type: str, ref: str, *, template_id,
                    language, length, user_id: str) -> None:
    """Background thread: chunk the source per section, generate, assemble."""
    from ai.notebooklm.md_templates import _length_instruction, get_md_template_role

    try:
        execute("UPDATE ai_tasks SET status='processing', updated_at=NOW() WHERE id=%s",
                (task_id,))
        _set_message(task_id, "⏳ Abriendo el material de origen…"
                     if source_type == "pdf" else "⏳ Leyendo el Markdown…")

        sections = _sections_for(source_type, ref)
        total = len(sections)
        _set_message(task_id, f"📄 {total} secciones detectadas")

        role = get_md_template_role(template_id) or ""
        length_hint = _length_instruction(length)
        provider = get_provider(PROVIDER)

        pieces: dict[int, str] = {}
        failed: list[dict] = []
        for i, section in enumerate(sections, start=1):
            n = i
            _set_message(task_id, f"✍️ Sección {n}/{total} · {section['title'][:60]}")
            try:
                body = _run_with_retry(
                    provider, section, n, total, language, length_hint, role=role,
                    on_attempt=lambda a, m, n=n: _set_message(
                        task_id, f"⚠️ Sección {n}/{total} falló (intento {a}/{MAX_ATTEMPTS}), reintentando…"),
                )
                pieces[n] = body
            except Exception as e:  # noqa: BLE001 — skip, keep the rest
                logger.error("[openzen] sección %d agotó los reintentos: %s", n, e)
                failed.append({"chunk": n, "title": section["title"], "error": str(e)})

            _save_state(task_id, {
                "chunks": [{"chunk": n, "title": section["title"],
                            "done": n in pieces, "failed": n in [f["chunk"] for f in failed]}
                           for n, section in enumerate(sections, start=1)],
                "failed_chunks": failed,
                "total": total,
                "generated": len(pieces),
            })

        if not pieces:
            raise RuntimeError(
                "Ninguna sección pudo generarse. Revisa la conexión con OpenZen."
            )

        content = _assemble(pieces)
        msg = f"✅ {len(pieces)}/{total} secciones generadas con OpenZen"
        if failed:
            msg += f" · ⚠️ {len(failed)} con aviso: " + ", ".join(
                f"#{f['chunk']}" for f in failed)
        _set_message(task_id, msg)

        stats = {
            "pipeline": f"openzen-{source_type} + {total} secciones",
            "model": PROVIDER, "depth": length, "language": language,
            "template_id": template_id,
            "source_type": source_type, "total_sections": total, "generated_sections": len(pieces),
            "failed_chunks": failed,
            "input_chars": sum(len(s["text"]) for s in sections),
            "output_chars": len(content),
        }
        execute(
            "UPDATE ai_tasks SET status='done', result_content=%s, coverage_data=%s,"
            " error_message=%s, completed_at=NOW(), updated_at=NOW() WHERE id=%s",
            (content, json.dumps(stats, ensure_ascii=False), msg, task_id),
        )
    except Exception as e:  # noqa: BLE001
        execute(
            "UPDATE ai_tasks SET status='error', error_message=%s,"
            " completed_at=NOW(), updated_at=NOW() WHERE id=%s",
            (str(e), task_id),
        )


# ─── Public API ─────────────────────────────────────────────────────
def _validate_block(block_id, topic_id, user_id):
    """Resolve a block to (id, topic_id, source_type, source_ref).

    ``source_ref`` is a file path for a PDF and the block's own text for a
    Markdown block, so the caller does not have to care which it got.
    """
    block_id_int = _coerce_id(block_id, "block_id")
    topic_id_int = _coerce_id(topic_id, "topic_id")
    block = query_one("SELECT * FROM blocks WHERE id = %s AND user_id = %s",
                      (block_id_int, user_id))
    if not block:
        raise ValueError("Block not found")

    btype = block["type"]
    if btype in PDF_TYPES:
        source_type = "pdf"
        ref = (block.get("url") or "").strip()
        if not ref:
            raise ValueError("El bloque PDF no tiene ruta de fichero")
    elif btype in MD_TYPES:
        source_type = "markdown"
        ref = (block.get("content") or "").strip()
        if not ref:
            raise ValueError("El bloque Markdown está vacío")
    else:
        raise ValueError(
            f"El bloque debe ser {'/'.join(PDF_TYPES + MD_TYPES)}, "
            f"es '{btype}'"
        )
    return block_id_int, topic_id_int, source_type, ref


def create_openzen_md_task(block_id, topic_id, user_id, *, template_id=None,
                           language="auto", length="standard") -> dict:
    """Create an OpenZen source→Markdown task, chunked per section."""
    block_id_int, topic_id_int, source_type, ref = _validate_block(
        block_id, topic_id, user_id)

    row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id,
             template_id, language, length, status)
           VALUES (%s, %s, %s, 'openzen', 'markdown', %s, %s, %s, %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, source_type, block_id,
         template_id, language, length),
    )
    task_id = row["id"]
    threading.Thread(
        target=_run_openzen_md,
        args=(task_id, source_type, ref),
        kwargs={"template_id": template_id, "language": language,
                "length": length, "user_id": user_id},
        daemon=True,
    ).start()
    return {"task_id": task_id}


def retry_openzen_chunk(task_id: str, chunk_num: int, user_id: str) -> dict:
    """Re-generate ONE failed section and splice it into the finished document.

    This is the "reiniciar el chunk" half of the warning. The document is
    already stored, so only the requested section is recomputed and the result
    is replaced in place between its markers.
    """
    task = query_one("SELECT * FROM ai_tasks WHERE id = %s AND user_id = %s",
                     (task_id, user_id))
    if not task:
        raise ValueError("Task not found")
    if task.get("task_type") != "openzen":
        raise ValueError("La tarea no es de OpenZen")
    content = task.get("result_content") or ""

    source_id = task.get("source_id")
    block = query_one("SELECT * FROM blocks WHERE id = %s AND user_id = %s",
                      (source_id, user_id))
    if not block:
        raise ValueError("Block not found")
    btype = block["type"]
    if btype in MD_TYPES:
        source_type, ref = "markdown", (block.get("content") or "").strip()
    else:
        source_type, ref = "pdf", (block.get("url") or "").strip()
    if not ref:
        raise ValueError("El bloque ya no tiene material de origen")

    sections = _sections_for(source_type, ref)
    n = int(chunk_num)
    if n < 1 or n > len(sections):
        raise ValueError(f"Sección {n} fuera de rango (1-{len(sections)})")

    from ai.notebooklm.md_templates import _length_instruction, get_md_template_role

    section = sections[n - 1]
    provider = get_provider(PROVIDER)
    body = _run_with_retry(
        provider, section, n, len(sections),
        task.get("language") or "auto",
        _length_instruction(task.get("length") or "standard"),
        # Same template role as the original run, or a retried section would
        # come back in a different voice than its neighbours.
        role=get_md_template_role(task.get("template_id")) or "",
    )

    new_content = _insert_chunk(content, n, body)

    state = _load_state(task_id)
    state["failed_chunks"] = [f for f in state.get("failed_chunks", [])
                              if f.get("chunk") != n]
    for c in state.get("chunks", []):
        if c.get("chunk") == n:
            c["done"], c["failed"] = True, False
    _save_state(task_id, state)

    msg = f"✅ Sección {n}/{len(sections)} regenerada"
    if state.get("failed_chunks"):
        msg += f" · ⚠️ siguen fallando: " + ", ".join(
            f"#{f['chunk']}" for f in state["failed_chunks"])
    execute(
        "UPDATE ai_tasks SET result_content=%s, coverage_data=%s, error_message=%s,"
        " updated_at=NOW() WHERE id=%s",
        (new_content, json.dumps(state, ensure_ascii=False), msg, task_id),
    )
    return {"task_id": task_id, "chunk": n, "remaining_failed": len(state.get("failed_chunks", []))}
