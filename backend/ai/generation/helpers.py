"""
Progress helpers and constants for background generation tasks.

Port of v2 ``backend/ai/generation/helpers.py`` (Phase 28 — Ollama removal)
to the v3 flat-import layout. Used by AI task runners to update the
``ai_tasks`` table with streaming progress, check cancellation, and build
structured checklist notifications for the frontend.

Exports:
    _check_cancelled, _build_progress, _build_data_block, _set_progress,
    _STEPS_CONTENT, _STEPS_TEST,
    provenance_banner, inject_provenance,
    markdown_provenance_quote
"""

import html
import json
import re
from datetime import datetime

from database import execute, query_one

# ─── Step lists ─────────────────────────────────────────────────────

_STEPS_CONTENT = [
    ("find_pdf",  "🔍 Buscar archivo PDF"),
    ("extract",   "📄 Extraer texto del PDF"),
    ("prepare",   "📐 Preparando generación..."),
    ("generate",  "🤖 Generando contenido..."),
    ("report",    "📊 Generando informe de cobertura"),
    ("insert",    "💾 Insertar bloque en el curso"),
]

_STEPS_TEST = [
    ("prepare",   "🔍 Preparando generación..."),
    ("generate",  "🤖 Generando test..."),
    ("done",      "✅ Test generado"),
]


# ─── Progress builders ──────────────────────────────────────────────


def _build_progress(current: int, extra: str = "",
                    steps: list | None = None) -> str:
    """Build a multi-line checklist progress string.

    Steps before ``current`` get ✅, current step gets ⏳ + extra,
    steps after get ☐. Pass ``steps`` for a custom step list
    (defaults to ``_STEPS_CONTENT``).
    """
    steps = steps or _STEPS_CONTENT
    lines = []
    for i, (_key, label) in enumerate(steps):
        if i < current:
            lines.append(f"✅ {label}")
        elif i == current:
            lines.append(f"⏳ {label} {extra}".strip())
        else:
            lines.append(f"☐ {label}")
    return "\n".join(lines)


def _build_data_block(pct: float | None = None,
                      eta_seconds: int | None = None) -> str:
    """Build an optional [DATA] block with progress estimation.

    Returns empty string if no pct (no estimation available), or a
    ``\n[DATA]{...}[/DATA]`` block that the frontend can parse to render
    a progress bar with ETA.

    Args:
        pct: Progress percentage 0.0–100.0 (None = no estimation).
        eta_seconds: Estimated remaining seconds (None = unknown).
    """
    if pct is None:
        return ""
    data: dict = {"pct": round(pct, 1)}
    if eta_seconds is not None:
        data["eta_seconds"] = eta_seconds
    return f"\n[DATA]{json.dumps(data)}[/DATA]"


def _set_progress(task_id: str, step: int, extra: str = "",
                  steps: list | None = None,
                  pct: float | None = None,
                  eta_seconds: int | None = None) -> None:
    """Update the full checklist progress for a task.

    Args:
        task_id: ai_tasks row id.
        step: Current step index (0-based).
        extra: Optional suffix text for the current step line.
        steps: Ordered list of ``(key, label)`` tuples.  Defaults to
               ``_STEPS_CONTENT``.
        pct: Progress percentage 0.0–100.0 (None = skip [DATA] block).
        eta_seconds: Estimated remaining seconds (used when pct is set).
    """
    msg = _build_progress(step, extra, steps=steps)
    msg += _build_data_block(pct, eta_seconds)
    execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (msg, task_id),
    )


def _check_cancelled(task_id: str) -> bool:
    """Check if a task has been cancelled in the DB."""
    row = query_one("SELECT status FROM ai_tasks WHERE id = %s", (task_id,))
    return row and row["status"] == "cancelled"


# ─── Sello de procedencia (Plantilla · Motor · Profundidad) ───────────

_PROV_DEPTH = {10: "⚡ Conciso", 20: "🔤 Estándar", 40: "📚 Detallado", 5: "🌱 Mini", 1: "🌱 Micro"}
# Estilo tipo cita (blockquote): borde izquierdo violeta, fondo slate claro,
# fuente itálica para que el sello se lea como una nota y no como una barra UI.
_PROV_STYLE = ("display:block;margin:0;padding:8px 14px;"
               "border-left:4px solid #7c3aed;background:#f8fafc;"
               "font:italic 500 12px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
               "color:#475569;")

# Mapeo de longitud → profundidad para el sello markdown (runner.py).
_MD_DEPTH_LABELS = {
    "concise":  "⚡ breve",
    "standard": "🔤 estándar",
    "detailed": "📚 detallado",
}

def provenance_banner(template: str, model_label: str, depth_label: str = "",
                      counts: str = "", skipped: list | None = None) -> str:
    """Una línea HTML estilo cita con el linaje de generación (plantilla, motor,
    profundidad, fechas). Se hornea en el result_content para que el bloque
    autoexplique cómo se generó. Todo se escapa vía html.escape."""
    parts = ["🧬 Procedencia · Plantilla: " + template, "Motor: " + model_label]
    if depth_label:
        parts.append(depth_label)
    if counts:
        parts.append(counts)
    if skipped:
        parts.append("⚠️ omitidos: " + ", ".join(skipped))
    parts.append(datetime.now().strftime("%Y-%m-%d %H:%M"))
    escaped = [html.escape(p) for p in parts]
    joined = " · ".join(escaped)
    return f'<div style="{_PROV_STYLE}">{joined}</div>'


def inject_provenance(html: str, banner_html: str) -> str:
    """Inserta el banner justo después de la primera apertura <body...>
    (insensible a mayúsculas). Si no hay <body>, lo antepone al documento."""
    out = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + banner_html, html, count=1, flags=re.I)
    if len(out) == len(html):
        return banner_html + html
    return out


def _md_depth_label(length: str | None) -> str:
    """Mapea la longitud del runner markdown a la etiqueta humana de profundidad.

    Longitudes conocidas: 'concise' / 'standard' / 'detailed'.
    Cualquier otro valor se devuelve crudo (defensa ante valores inesperados).
    """
    if not length:
        return "🔤 estándar"
    return _MD_DEPTH_LABELS.get(length, length)


def markdown_provenance_quote(template_id: str | None,
                              provider_label: str,
                              depth_label: str,
                              extra: str = "") -> str:
    """Devuelve UNA línea markdown en formato blockquote con el sello de
    procedencia para los bloques generados por pipelines markdown.

    Formato::
        > 🧬 **Procedencia** · Plantilla: {template_id or 'por defecto'} ·
          Motor: {provider_label} · Profundidad: {depth_label}
          {' · ' + extra if extra else ''}

    Devuelve siempre la línea + un salto de línea en blanco para que el
    contenido empiece en su propio párrafo. El texto NO se escapa: es markdown
    plano (los caracteres especiales se renderizan tal cual en cualquier visor
    de markdown).

    Args:
        template_id: id de la plantilla (MD_TEMPLATES) o None → 'por defecto'.
        provider_label: etiqueta del motor (p. ej. 'opencode-acp').
        depth_label: etiqueta humana de profundidad (p. ej. '🔤 estándar').
        extra: sufijo libre (p. ej. fecha) que se añade tras ' · ' si se
            facilita.
    """
    tmpl = (template_id or "por defecto").strip() or "por defecto"
    line = (
        f"> 🧬 **Procedencia** · Plantilla: {tmpl} · "
        f"Motor: {provider_label} · Profundidad: {depth_label}"
    )
    if extra:
        line += f" · {extra}"
    return line + "\n\n"