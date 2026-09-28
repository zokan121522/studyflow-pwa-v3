"""
Prompts for NotebookLM content generation.

These are sent to Gemini via the Chat API (50 queries/day).

v3 split note: v2 kept ~680 lines in this module; v3 keeps only the
prompts consumed by the immediate pipeline (PDF→Markdown, PDF→HTML)
plus ``compose_nb_md_prompt``. Task-specific prompts (YouTube, Audio,
Test, Infographic) are ported next to their own modules in later
sub-phases, keeping every module under 500 lines.

Exports:
    PDF_TO_MARKDOWN_PROMPT, PDF_TO_HTML_PROMPT
    compose_nb_md_prompt
"""

import logging

logger = logging.getLogger(__name__)

# ─── compose (Phase 62 — shared markdown config modal) ──────────────


def compose_nb_md_prompt(
    template_id: str | None,
    language: str,
    length: str,
    base_prompt: str,
) -> str:
    """Compose the NotebookLM MD prompt with optional template/language/length overrides.

    Defaults (template_id=None, language='auto', length='standard') return
    ``base_prompt`` UNCHANGED so pre-config-modal behaviour is preserved
    exactly (no regression). Any non-default parameter prepends a
    configuration block that steers Gemini toward the chosen role,
    language and verbosity while keeping the original exhaustive rules
    intact.

    v3 note: the template role overrides live in ``ai.generation.prompts``
    which arrives with the md-template feature in a later sub-phase. Until
    then, non-default language/length still apply; unknown template roles
    degrade gracefully to the base prompt with a warning.

    Args:
        template_id: MD_TEMPLATES key (or None). Unknown → ignored (no role).
        language: 'auto' | 'es' | 'en'.
        length: 'concise' | 'standard' | 'detailed'.
        base_prompt: The exhaustive base prompt (e.g. PDF_TO_MARKDOWN_PROMPT).

    Returns:
        The composed system prompt string.
    """
    is_default = (
        not template_id
        and (language or "auto") == "auto"
        and (length or "standard") == "standard"
    )
    if is_default:
        return base_prompt

    role = ""
    length_instruction = ""
    try:
        # Lazy import: avoids circulars and defers the dep until used.
        from ai.generation.prompts import (  # type: ignore
            _length_instruction,
            get_md_template_role,
        )

        role = get_md_template_role(template_id)
        if template_id and not role:
            role = get_md_template_role("notas-estandar")
        length_instruction = _length_instruction(length)
    except ImportError:
        logger.warning(
            "ai.generation.prompts not available yet — applying language/length "
            "override without template role (template_id=%r)",
            template_id,
        )

    parts: list[str] = []
    if role:
        parts.append(role)
    if (language or "auto") != "auto":
        parts.append(
            f"\n## LANGUAGE OVERRIDE\n"
            f"You MUST reply in language code: '{language}'.\n"
        )
    if (length or "standard") != "standard":
        parts.append("\n## LENGTH OVERRIDE\n" + length_instruction)
    parts.append("\n## BASE RULES (always apply)\n" + base_prompt)
    return "".join(parts)


# ─── PDF → Markdown ─────────────────────────────────────────────────

PDF_TO_MARKDOWN_PROMPT = (
    "# INSTRUCCIÓN: CONVERSIÓN EXHAUSTIVA PDF → MARKDOWN\n\n"
    "Convierte el contenido de este PDF a Markdown. "
    "SIGUE ESTRICTAMENTE LAS SIGUIENTES REGLAS:\n\n"
    "1. **NO RESUMAS, NO OMITAS NADA, NO SIMPLIFIQUES.** "
    "Debes incluir ABSOLUTAMENTE TODO el contenido del PDF "
    "sin excluir ninguna sección, párrafo, frase, ejemplo, tabla, "
    "actividad, nota al pie, referencia bibliográfica, anexo o índice.\n"
    "2. **COMPLETITUD TOTAL:** Si el PDF tiene 50 páginas, el Markdown "
    "debe reflejar las 50 páginas. No hay límite de extensión.\n"
    "3. **ESTRUCTURA JERÁRQUICA:** Usa # (título principal), ## (capítulos), "
    "### (secciones), #### (subsecciones). Respeta la jerarquía original "
    "del documento.\n"
    "4. **TABLAS COMPLETAS:** Convierte TODAS las tablas a pipe tables "
    "(| col1 | col2 |). Incluye fila de cabecera, separador (|:---|:---|), "
    "y TODAS las filas de datos. No trunquees ni resumas ninguna tabla.\n"
    "5. **LISTAS Y VIÑETAS:** Conserva todas las listas ordenadas y no "
    "ordenadas textualmente.\n"
    "6. **EJEMPLOS Y ACTIVIDADES:** Incluye cada ejemplo, ejercicio, "
    "actividad o caso práctico con su enunciado completo.\n"
    "7. **NOTAS, ADVERTENCIAS, RECUADROS:** Preserva todo el contenido "
    "de callouts, notas al margen, warnings, tips, etc.\n"
    "8. **CÓDIGO Y FÓRMULAS:** Usa bloques de código (```) para código "
    "fuente y $...$ o $$...$$ para fórmulas matemáticas.\n"
    "9. **REFERENCIAS Y BIBLIOGRAFÍA:** Incluye la lista completa de "
    "referencias bibliográficas, webs, footnotes o citas al final.\n"
    "10. **SIN MARCAS DE NOTA:** Omite los números entre corchetes "
    "[1], [2], [3]... que aparezcan en el cuerpo del texto "
    "(marcadores de nota al pie, cita o referencia). "
    "No los incluyas en el Markdown.\n"
    "11. **FORMATO LIMPIO:** No añadas comentarios, metadatos, ni texto "
    "que no esté en el original. No resumas nada.\n\n"
    "IMPORTANTE: Si el contenido es extenso, tu respuesta debe ser "
    "igualmente extensa. No hay restricción de longitud."
)

# ─── PDF → HTML ─────────────────────────────────────────────────────

PDF_TO_HTML_PROMPT = (
    "# INSTRUCCIÓN: CONVERSIÓN EXHAUSTIVA PDF → HTML ESTRUCTURADO\n\n"
    "Genera HTML semántico a partir del contenido de este PDF. "
    "Debes incluir ABSOLUTAMENTE TODO el contenido sin omitir nada.\n\n"
    "## REGLAS DE COMPLETITUD\n\n"
    "1. **NO RESUMAS NADA.** Debes incluir ABSOLUTAMENTE TODO el "
    "contenido del PDF: cada párrafo, cada frase, cada ejemplo, "
    "cada tabla completa, cada actividad, cada nota, cada referencia.\n"
    "2. **FRAGMENTO HTML:** Genera SOLO el contenido del <body>. "
    "NO incluyas <html>, <head>, <style>, <body> ni scripts.\n"
    "3. **HTML5 SEMÁNTICO:** Usa <section id=\"...\">, <h2>, <h3>, <h4>, "
    "<p>, <ul>, <ol>, <table>, <pre><code>.\n"
    "4. **EXTENSIÓN:** Si el PDF es largo, tu HTML debe ser "
    "igualmente largo. No hay límite de tamaño.\n\n"
    "## ESTRUCTURA DEL DOCUMENTO (orden recomendado)\n\n"
    "Usa esta estructura general de secciones adaptándola al contenido del PDF:\n\n"
    "- **Título y subtítulo:** <h1>🔐 Título del tema</h1> + "
    "<p class=\"subtitle\"><strong>Asignatura · Curso</strong><br />Descripción breve</p>\n"
    "- **Índice (TOC):** <div class=\"toc\"><h3>📋 Índice de Contenidos</h3>"
    "<ol><li>...</li></ol></div> — con <ol> anidados para subsecciones. "
    "Siempre que haya más de 3 secciones.\n"
    "- **Objetivos:** <h2>1. Objetivos</h2> + <ol class=\"obj-list\"><li>"
    "<strong>Verbo</strong> descripción</li></ol>\n"
    "- **Introducción/Contexto:** <h2>2. Introducción</h2> + párrafos. "
    "Para resúmenes conceptuales: <div class=\"intro-box\"><ul><li>...</li></ul></div>\n"
    "- **Secciones principales:** Numeradas: <h2>3. Tema Principal</h2>, "
    "<h3>3.1 Subtema</h3>, <h4>3.1.1 Detalle</h4>\n"
    "- **Resumen final:** <h2>Resumen</h2> + <div class=\"summary-grid\">"
    "con <div class=\"summary-card\"><div class=\"tool\">Nombre</div>"
    "<div class=\"desc\">Descripción</div></div>\n"
    "- **Comandos o referencia rápida:** <h2>Comandos de Referencia</h2> + "
    "<table class=\"cmd-table\">\n"
    "- **Bibliografía:** <h2>Bibliografía</h2> + "
    "<div class=\"ref-section\"><ul><li>...</li></ul></div>\n"
    "- **Tareas/Actividades:** Al final del documento: "
    "<div class=\"hw-box\"><strong>📋 Tareas Pendientes</strong>"
    "<ul><li>...</li></ul></div>\n\n"
    "## PATRONES DE COMPONENTES VISUALES\n\n"
    "Usa los siguientes patrones HTML cuando el contenido lo requiera:\n\n"
    "### Tablas\n"
    "Usa <table><thead><tr><th>...</th></tr></thead>"
    "<tbody><tr><td>...</td></tr></tbody></table>. "
    "Incluye TODAS las filas y columnas. NUNCA uses pipe tables (| col |).\n\n"
    "### Callouts (notas, advertencias, éxito)\n"
    "- <div class=\"note\"><strong>💡 Título</strong> texto</div> → notas informativas\n"
    "- <div class=\"warning\"><strong>⚠️ Título</strong> texto</div> → advertencias\n"
    "- <div class=\"success\"><strong>✅ Título</strong> texto</div> → ventajas/resultados\n\n"
    "### Código\n"
    "Usa <pre><code>bloque de código</code></pre> para código fuente. "
    "<code>inline code</code> para referencias a comandos o rutas.\n\n"
    "### Pasos numerados (procedimientos)\n"
    "<ol class=\"steps\">"
    "<li><span class=\"step-num\">1</span><strong>Paso</strong>"
    "<p>Descripción</p><pre><code>comando</code></pre></li>"
    "</ol>\n\n"
    "### Cuadrícula visual (permisos, valores)\n"
    "<div class=\"perm-visual\">"
    "<div class=\"perm-card\"><div class=\"num\">7</div>"
    "<div class=\"sym\">rwx</div><div class=\"desc\">Descripción</div></div>"
    "</div>\n\n"
    "### Reglas de firewall (UFW)\n"
    "<div class=\"ufw-example\">"
    "<span class=\"ufw-tag allow\">ALLOW</span>"
    "<code>comando</code> → descripción"
    "</div>\n\n"
    "### Configuración (Samba, etc.)\n"
    "<div class=\"samba-config\">"
    "<span class=\"section\">[Sección]</span><br />"
    "&nbsp;&nbsp;<span class=\"directive\">clave</span> = "
    "<span class=\"value\">valor</span>"
    "</div>\n\n"
    "## CLASES CSS DISPONIBLES\n\n"
    "NO inventes otras clases. Usa solo:\n"
    ".subtitle, .toc, .obj-list, .intro-box, "
    ".note, .warning, .success, .hw-box, "
    ".summary-grid, .summary-card, .cmd-table, "
    ".steps, .step-num, .perm-visual, .perm-card, "
    ".ufw-example, .ufw-tag, .ufw-tag.allow, .ufw-tag.deny, "
    ".samba-config, .samba-config .section, "
    ".samba-config .directive, .samba-config .value, "
    ".ref-section, .nav-links.\n\n"
    "## REGLAS DE CALIDAD\n\n"
    "- **NO AÑADAS TEXTO PROPIO:** No incluyas comentarios, "
    "resúmenes, opiniones o metadatos que no estén en el original.\n"
    "- **SIN MARCAS DE NOTA [N]:** Omite los números entre corchetes "
    "[1], [2], [3]... que aparezcan en el cuerpo del texto "
    "(marcadores de nota al pie, cita o referencia). "
    "No los incluyas en el HTML.\n"
    "- **TABLAS COMPLETAS:** Cada fila y columna del PDF debe estar en tu HTML.\n"
    "- **BIBLIOGRAFÍA COMPLETA:** Incluye todas las referencias al final.\n"
    "- **EJEMPLOS Y ACTIVIDADES:** Preserva cada uno con su enunciado completo."
)