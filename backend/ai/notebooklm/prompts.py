"""
Prompts for NotebookLM content generation.

These are sent to Gemini via the Chat API (50 queries/day limit).

Exports:
    PDF_TO_MARKDOWN_PROMPT, PDF_TO_HTML_PROMPT
    YOUTUBE_TO_MARKDOWN_PROMPT, YOUTUBE_TO_HTML_PROMPT
    AUDIO_SCRIPT_PROMPT, AUDIO_SCRIPT_PROMPT_DURATION, TEST_GENERATION_PROMPT
"""


def compose_nb_md_prompt(
    template_id: str | None,
    language: str,
    length: str,
    base_prompt: str,
) -> str:
    """Compose the NotebookLM MD prompt with optional template/language/length overrides.

    Phase 62 — Shared markdown config modal (issue #253).
    Defaults (template_id=None, language='auto', length='standard') return
    ``base_prompt`` UNCHANGED so pre-Phase-62 behaviour is preserved
    exactly (no regression). Any non-default parameter prepends a
    configuration block that steers Gemini toward the chosen role,
    language and verbosity while keeping the original exhaustive rules
    intact.

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

    # Lazy import: ai.notebooklm.md_templates is a v3 port of the pieces
    # of generation.prompts that compose_nb_md_prompt needs.
    from ai.notebooklm.md_templates import (
        _length_instruction,
        get_md_template_role,
    )

    role = get_md_template_role(template_id)
    # Mirror markdown-to-opencode fallback: unknown template → 'notas-estandar'
    if template_id and not role:
        role = get_md_template_role("notas-estandar")
    parts: list[str] = []
    if role:
        parts.append(role)
    if (language or "auto") != "auto":
        parts.append(
            f"\n## LANGUAGE OVERRIDE\n"
            f"You MUST reply in language code: '{language}'.\n"
        )
    if (length or "standard") != "standard":
        parts.append("\n## LENGTH OVERRIDE\n" + _length_instruction(length))
    parts.append("\n## BASE RULES (always apply)\n" + base_prompt)
    return "".join(parts)

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

# ── YouTube ─────────────────────────────────────────────────────────

YOUTUBE_TO_MARKDOWN_PROMPT = (
    "# INSTRUCTION: YOUTUBE VIDEO → STRUCTURED COURSE NOTES (MARKDOWN)\n\n"
    "Transcribe and structure the content of this YouTube video into clean, "
    "well-organized markdown suitable for academic study.\n\n"
    "## RULES\n\n"
    "1. **EXHAUSTIVE:** Include ALL key points, concepts, examples, and "
    "demonstrations from the video. Do NOT summarize or omit anything important.\n"
    "2. **STRUCTURE:** Use # Title, ## Main Sections, ### Subsections. "
    "Organize the content logically as a course lesson.\n"
    "3. **INTRODUCTION:** Start with a brief context: topic, relevance, "
    "and what the video covers.\n"
    "4. **KEY CONCEPTS:** For each major concept: define it, explain it, "
    "and include the instructor's examples.\n"
    "5. **CODE / COMMANDS:** If the video shows code, commands, or "
    "configurations, include them in ``` blocks.\n"
    "6. **VISUAL DESCRIPTIONS:** If the video uses diagrams, slides, or "
    "visuals, describe what they show in words.\n"
    "7. **SUMMARY:** End with a concise summary of the key takeaways.\n"
    "8. **FORMAT:** Clean markdown. Use | tables | for comparisons. "
    "Use **bold** for key terms. Use bullet lists for enumerations.\n"
    "9. **LANGUAGE:** Match the video's language. If the video is in "
    "Spanish, write in Spanish. If in English, write in English.\n\n"
    "IMPORTANT: Your response must be as long as needed to cover all "
    "content from the video. No length limit."
)

YOUTUBE_TO_HTML_PROMPT = (
    "# INSTRUCTION: YOUTUBE VIDEO → DARK-THEMED HTML\n\n"
    "Transcribe and structure this YouTube video into a well-formatted "
    "HTML document with dark theme styling. Generate ONLY the <body> "
    "fragment — do NOT include <html>, <head>, <style>, or <body> tags.\n\n"
    "## RULES\n\n"
    "1. **EXHAUSTIVE:** Include ALL content from the video — every concept, "
    "example, demonstration. No summaries, no omissions.\n"
    "2. **SEMANTIC HTML5:** Use <section id=\"...\">, <h2>, <h3>, <h4>, "
    "<p>, <ul>, <ol>, <pre><code>.\n"
    "3. **STRUCTURE:** <h1>Video Title</h1> + <p class=\"subtitle\">description</p> "
    "+ <div class=\"toc\">table of contents</div> + sections + "
    "<div class=\"summary-grid\">final summary</div>.\n"
    "4. **CALL OUT KEY TERMS:** Use <div class=\"note\"> for important "
    "concepts, <div class=\"warning\"> for common mistakes.\n"
    "5. **CODE BLOCKS:** Use <pre><code> for commands, code snippets, "
    "and configuration files shown in the video.\n"
    "6. **TABLES:** Use <table><thead><tr><th>...</th></tr></thead>"
    "<tbody>...</tbody></table> for comparisons.\n"
    "7. **LANGUAGE:** Match the video's language.\n\n"
    "Use ONLY these CSS classes: .subtitle, .toc, .note, .warning, "
    ".success, .summary-grid, .summary-card, .hw-box, .obj-list."
)


# ── Audio Script ──────────────────────────────────────────────────────

AUDIO_SCRIPT_PROMPT = (
    "# INSTRUCTION: CONTENT → NATURAL AUDIO SCRIPT\n\n"
    "Transform the following academic content into a natural, "
    "conversational audio script suitable for text-to-speech narration.\n\n"
    "## RULES\n\n"
     "1. **CONVERSATIONAL TONE:** Write as if a teacher is explaining "
     "the topic to a student. Use natural Spanish transitions like "
     "'Ahora veamos…', 'Otro concepto importante es…', "
     "'Para recapitular…'\n"
    "2. **COMPLETE COVERAGE:** Cover ALL key concepts, definitions, "
    "examples, and steps from the source content. Do NOT omit anything "
    "important.\n"
    "3. **NO MARKDOWN:** Output plain text only. No markdown formatting, "
    "no headings, no bullet points, no asterisks, no bold/italic markers.\n"
    "4. **NO CODE BLOCKS:** Explain code verbally. For example, instead "
    "of showing `print('hello')`, say 'We use the print function to "
    "display the text hello'.\n"
    "5. **NO HTML:** No HTML tags of any kind. Plain text only.\n"
    "6. **SPOKEN NUMBERS:** Write numbers as words where appropriate "
    "(e.g., 'fifteen' instead of '15') for better TTS flow.\n"
    "7. **PARAGRAPHS:** Use short paragraphs separated by blank lines. "
    "Each paragraph should be one coherent idea.\n"
     "8. **LANGUAGE:** Always output in Spanish, regardless of the source "
     "language. If the source has technical terms in English (e.g., "
     "'function', 'variable', 'database'), keep them as-is but explain "
     "them in Spanish.\n"
    "9. **LENGTH:** The output should be proportional to the input. "
    "A 2000-word source should produce approximately 1500-2500 words of script.\n\n"
    "IMPORTANT: Output ONLY the script text. No preamble, no explanation, "
    "no metadata."
)

AUDIO_SCRIPT_PROMPT_EN = (
    "# INSTRUCTION: CONTENT → NATURAL AUDIO SCRIPT (ENGLISH)\n\n"
    "Transform the following academic content into a natural, "
    "conversational audio script suitable for text-to-speech narration.\n\n"
    "## RULES\n\n"
    "1. **CONVERSATIONAL TONE:** Write as if a teacher is explaining "
    "the topic to a student. Use natural English transitions like "
    "'Now let's look at…', 'Another important concept is…', "
    "'To summarize…'\n"
    "2. **COMPLETE COVERAGE:** Cover ALL key concepts, definitions, "
    "examples, and steps from the source content. Do NOT omit anything "
    "important.\n"
    "3. **NO MARKDOWN:** Output plain text only. No markdown formatting, "
    "no headings, no bullet points, no asterisks, no bold/italic markers.\n"
    "4. **NO CODE BLOCKS:** Explain code verbally. For example, instead "
    "of showing `print('hello')`, say 'We use the print function to "
    "display the text hello'.\n"
    "5. **NO HTML:** No HTML tags of any kind. Plain text only.\n"
    "6. **SPOKEN NUMBERS:** Write numbers as words where appropriate "
    "(e.g., 'fifteen' instead of '15') for better TTS flow.\n"
    "7. **PARAGRAPHS:** Use short paragraphs separated by blank lines. "
    "Each paragraph should be one coherent idea.\n"
    "8. **LANGUAGE:** Always output in English, regardless of the source "
    "language. If the source has technical terms in other languages, "
    "keep them as-is but explain them in English.\n"
    "9. **LENGTH:** The output should be proportional to the input. "
    "A 2000-word source should produce approximately 1500-2500 words of script.\n\n"
    "IMPORTANT: Output ONLY the script text. No preamble, no explanation, "
    "no metadata."
)

# Duration-tuned variants: the script is rewritten (via OpenZEN) to fit a
# fixed narration time instead of reading the source verbatim. Placeholders:
#   {minutes} → target duration in minutes ("5", "10", "20")
#   {words}   → approximate word budget (~150 words/min TTS)
# audio.py formats them with .format(minutes=..., words=...).

AUDIO_SCRIPT_PROMPT_DURATION = (
    "# INSTRUCCIÓN: CONTENIDO → GUION DE AUDIO CONDENSADO ({minutes} min)\n\n"
    "Transforma el siguiente contenido académico en un guion de audio "
    "natural y conversacional, adecuado para narración por texto-a-voz.\n\n"
    "## REGLAS\n\n"
    "1. **DURACIÓN OBJETIVO:** La narración final debe durar "
    "aproximadamente {minutes} minutos. Escribe como máximo unas "
    "{words} palabras; este es el límite más importante.\n"
    "2. **TONO CONVERSACIONAL:** Escribe como si un profesor explicara "
    "el tema a un estudiante. Usa transiciones naturales en español como "
    "'Ahora veamos…', 'Otro concepto importante es…', 'Para recapitular…'\n"
    "3. **COBERTURA PRIORITARIA:** En {minutes} minutos no puedes "
    "cubrirlo todo. Prioriza: definiciones clave, ideas principales, "
    "ejemplos ilustrativos y pasos esenciales. Omite detalles secundarios "
    "o excesivamente técnicos.\n"
    "4. **SIN MARKDOWN:** Solo texto plano. Sin formato markdown, "
    "sin encabezados, sin viñetas, sin asteriscos, sin negritas/cursivas.\n"
    "5. **SIN BLOQUES DE CÓDIGO:** Explica el código verbalmente. Por "
    "ejemplo, en lugar de mostrar `print('hola')`, di 'Usamos la función "
    "print para mostrar el texto hola'.\n"
    "6. **SIN HTML:** Ninguna etiqueta HTML de ningún tipo. Solo texto plano.\n"
    "7. **NÚMEROS HABLADOS:** Escribe los números como palabras cuando "
    "proceda (p. ej., 'quince' en lugar de '15') para un mejor flujo TTS.\n"
    "8. **PÁRRAFOS CORTOS:** Usa párrafos breves separados por líneas en "
    "blanco. Cada párrafo debe contener una única idea coherente.\n"
    "9. **IDIOMA:** Escribe siempre en español, independientemente del "
    "idioma de origen. Si el contenido tiene términos técnicos en inglés "
    "(p. ej., 'function', 'variable', 'database'), consérvalos tal cual "
    "pero explícalos en español.\n\n"
    "IMPORTANTE: No superes las {words} palabras. La narración debe "
    "encajar en {minutes} minutos a ritmo natural (unas 150 palabras por "
    "minuto). Emite SOLO el guion: sin preámbulos, sin explicaciones, "
    "sin metadatos."
)

AUDIO_SCRIPT_PROMPT_DURATION_EN = (
    "# INSTRUCTION: CONTENT → CONDENSED AUDIO SCRIPT ({minutes} min)\n\n"
    "Transform the following academic content into a natural, "
    "conversational audio script for text-to-speech narration.\n\n"
    "## RULES\n\n"
    "1. **TARGET DURATION:** The final narration must last approximately "
    "{minutes} minutes. Write at most about {words} words; this is the "
    "most important constraint.\n"
    "2. **CONVERSATIONAL TONE:** Write as if a teacher is explaining the "
    "topic to a student. Use natural English transitions like "
    "'Now let's look at…', 'Another important concept is…', 'To summarize…'\n"
    "3. **PRIORITIZED COVERAGE:** You cannot cover everything in "
    "{minutes} minutes. Prioritize: key definitions, main ideas, "
    "illustrative examples, and essential steps. Omit secondary or overly "
    "technical details.\n"
    "4. **NO MARKDOWN:** Output plain text only. No markdown formatting, "
    "no headings, no bullet points, no asterisks, no bold/italic markers.\n"
    "5. **NO CODE BLOCKS:** Explain code verbally. For example, instead "
    "of showing `print('hello')`, say 'We use the print function to "
    "display the text hello'.\n"
    "6. **NO HTML:** No HTML tags of any kind. Plain text only.\n"
    "7. **SPOKEN NUMBERS:** Write numbers as words where appropriate "
    "(e.g., 'fifteen' instead of '15') for better TTS flow.\n"
    "8. **SHORT PARAGRAPHS:** Use short paragraphs separated by blank "
    "lines. Each paragraph should be one coherent idea.\n"
    "9. **LANGUAGE:** Always output in English, regardless of the source "
    "language. If the source has technical terms in other languages, "
    "keep them as-is but explain them in English.\n\n"
    "IMPORTANT: Do not exceed {words} words. The narration must fit in "
    "{minutes} minutes at a natural reading pace (about 150 words per "
    "minute). Output ONLY the script: no preamble, no explanation, no "
    "metadata."
)


# ── Rich Structured Transcript ────────────────────────────────────────
# Used by the audio pipeline: after generating the TTS script, OpenZEN is
# asked a second question to produce a rich, structured Markdown version
# of the same content. The frontend offers inserting it alongside the MP3.

TRANSCRIPT_PROMPT = (
    "# INSTRUCTION: CONTENT → RICH STRUCTURED MARKDOWN\n\n"
    "Transform the following academic content into a rich, well-structured "
    "Markdown study note. This will be inserted into a course as the "
    "transcription of an audio lesson.\n\n"
    "## RULES\n\n"
    "1. **STRUCTURE:** Use clear Markdown headings (## for main sections, "
    "### for subsections).\n"
    "2. **RICH FORMATTING:** Use bold for key terms, bullet/numbered lists "
    "for enumerations, fenced code blocks for code, and tables when "
    "comparing data.\n"
    "3. **COMPLETE COVERAGE:** Cover ALL key concepts, definitions, "
    "examples, and steps from the source content. Do NOT omit anything "
    "important.\n"
    "4. **READABLE:** Write in a clear study-note style, organized "
    "logically. Use short paragraphs.\n"
    "5. **LANGUAGE:** Always output in Spanish, regardless of the source "
    "language. If the source has technical terms in English (e.g., "
    "'function', 'variable', 'database'), keep them as-is but explain "
    "them in Spanish.\n"
    "6. **LENGTH:** The output should be proportional to the input. "
    "A 2000-word source should produce approximately 1500-2500 words.\n\n"
    "IMPORTANT: Output ONLY the Markdown note. No preamble, no "
    "explanation, no metadata."
)

TRANSCRIPT_PROMPT_EN = (
    "# INSTRUCTION: CONTENT → RICH STRUCTURED MARKDOWN (ENGLISH)\n\n"
    "Transform the following academic content into a rich, well-structured "
    "Markdown study note. This will be inserted into a course as the "
    "transcription of an audio lesson.\n\n"
    "## RULES\n\n"
    "1. **STRUCTURE:** Use clear Markdown headings (## for main sections, "
    "### for subsections).\n"
    "2. **RICH FORMATTING:** Use bold for key terms, bullet/numbered lists "
    "for enumerations, fenced code blocks for code, and tables when "
    "comparing data.\n"
    "3. **COMPLETE COVERAGE:** Cover ALL key concepts, definitions, "
    "examples, and steps from the source content. Do NOT omit anything "
    "important.\n"
    "4. **READABLE:** Write in a clear study-note style, organized "
    "logically. Use short paragraphs.\n"
    "5. **LANGUAGE:** Always output in English, regardless of the source "
    "language. If the source has technical terms in other languages, "
    "keep them as-is but explain them in English.\n"
    "6. **LENGTH:** The output should be proportional to the input. "
    "A 2000-word source should produce approximately 1500-2500 words.\n\n"
    "IMPORTANT: Output ONLY the Markdown note. No preamble, no "
    "explanation, no metadata."
)


# ── Test Generation ───────────────────────────────────────────────────

TEST_GENERATION_PROMPT = (
    "# INSTRUCTION: GENERATE MULTIPLE-CHOICE TEST\n\n"
    "Create a comprehensive multiple-choice test based on the following "
    "academic content. The test must assess understanding of ALL key "
    "concepts, not just surface-level recall.\n\n"
    "## OUTPUT FORMAT\n\n"
    "Return a valid JSON array of question objects. "
    "NO markdown, NO code fences, NO extra text — ONLY the JSON array.\n\n"
    "Each question object:\n"
    "{{\n"
    '  "question": "Question text?",\n'
    '  "options": ["Option A", "Option B", "Option C", "Option D"],\n'
    '  "correct": 0,\n'
    '  "explanation": "Why this is correct"\n'
    "}}\n\n"
    "- `correct` is the 0-based index of the correct option.\n"
    "- `explanation` explains WHY the answer is correct.\n\n"
    "## COVERAGE RULES\n\n"
    "1. **BREADTH:** Cover ALL major topics and subtopics from the content.\n"
    "2. **DEPTH:** Include a mix of: definition questions, comprehension "
    "questions, application questions, and analysis questions.\n"
    "3. **DISTRACTORS:** Each wrong option should be plausible but clearly "
    "incorrect to someone who understands the material.\n"
    "4. **NO TRICK QUESTIONS:** Every question should be fair and "
    "directly answerable from the content.\n"
    "5. **QUANTITY:** Generate EXACTLY {num_questions} questions. "
    "Do NOT generate more or fewer — the array must contain exactly "
    "{num_questions} question objects.\n\n"
    "## LANGUAGE\n\n"
    "Match the source content's language. If the source is in Spanish, "
    "questions and explanations must be in Spanish. If in English, "
    "in English.\n\n"
    "CRITICAL: Return ONLY the JSON array. No other text.")


def make_test_prompt(num_questions: int, block_title: str = "") -> str:
    """TEST_GENERATION_PROMPT with an exact per-block question count.

    Used by the multi-block test flow (Phase 45): one call per block with
    its exact quota, so the final distribution is mathematically even
    regardless of what the model decides.

    Args:
        num_questions: Exact number of questions this block must produce.
        block_title: Optional block title, prepended as context.
    """
    prompt = TEST_GENERATION_PROMPT.format(num_questions=num_questions)
    if block_title:
        return (
            "# SOURCE TITLE\n\n"
            f"The content below corresponds to the block: **{block_title}**.\n\n"
            + prompt
        )
    return prompt


# ── Markdown Enhancement (✨ Nb MD) ──────────────────────────────────

ENHANCE_MD_PROMPT = (
    "# INSTRUCCIÓN: MEJORA EXHAUSTIVA DE MARKDOWN\n\n"
    "Revisa y mejora el siguiente contenido Markdown. "
    "Tu tarea es mejorar la calidad del Markdown "
    "sin cambiar su significado ni perder absolutamente nada.\n\n"
    "## REGLAS DE COMPLETITUD\n\n"
    "1. **NO RESUMAS, NO OMITAS NADA, NO SIMPLIFIQUES.** "
    "Debes incluir ABSOLUTAMENTE TODO el contenido original "
    "sin excluir ninguna sección, párrafo, frase, ejemplo, tabla, "
    "lista, código o referencia.\n"
    "2. **COMPLETITUD TOTAL:** Si el contenido tiene 1000 líneas, "
    "el Markdown mejorado debe reflejar las 1000 líneas. "
    "No hay límite de extensión.\n"
    "3. **ESTRUCTURA JERÁRQUICA:** Asegura una jerarquía correcta "
    "de encabezados (# → ## → ### → ####). "
    "Corrige sangrías rotas o inconsistentes en listas y contenido anidado.\n"
    "4. **TABLAS COMPLETAS:** Repara pipe tables rotas. "
    "Asegura que todas las columnas estén alineadas y "
    "cada fila tenga el número correcto de celdas. "
    "No trunquees ni resumas ninguna tabla.\n"
    "5. **LISTAS Y VIÑETAS:** Conserva todas las listas ordenadas "
    "y no ordenadas textualmente con su anidación correcta.\n"
    "6. **BLOQUES DE CÓDIGO:** Asegura que los bloques ``` tengan "
    "etiquetas de lenguaje cuando sean identificables. "
    "Corrige código mal escapado.\n"
    "7. **FÓRMULAS:** Asegura que los bloques $$...$$ y $...$ "
    "estén correctamente formateados.\n"
    "8. **LEGIBILIDAD:** Añade líneas en blanco entre secciones "
    "para separación visual. Usa formato consistente en todo el documento.\n"
    "9. **SIN AÑADIDOS:** No añadas contenido nuevo, comentarios, "
    "opiniones o metadatos que no estuvieran en el original.\n"
    "10. **SIN HTML:** Solo Markdown puro. Nada de etiquetas HTML.\n\n"
    "## IDIOMA\n\n"
    "Respeta el idioma del contenido fuente. Si el original está "
    "en español, el resultado debe estar en español. Si en inglés, "
    "en inglés. Y así con cualquier idioma.\n\n"
    "IMPORTANTE: Si el contenido es extenso, tu respuesta debe ser "
    "igualmente extensa. No hay restricción de longitud.\n\n"
    "CRÍTICO: Devuelve SOLO el Markdown mejorado. Sin preámbulos, "
    "sin explicaciones, sin metadatos."
)

# ── Markdown → HTML (✨ Nb HTML) ───────────────────────────────────

MD_TO_HTML_PROMPT = (
    "# INSTRUCCIÓN: CONVERSIÓN EXHAUSTIVA MARKDOWN → HTML ESTRUCTURADO\n\n"
    "Genera HTML semántico a partir del siguiente contenido Markdown. "
    "Debes incluir ABSOLUTAMENTE TODO el contenido sin omitir nada.\n\n"
    "## REGLAS DE COMPLETITUD\n\n"
    "1. **NO RESUMAS NADA.** Debes incluir ABSOLUTAMENTE TODO el "
    "contenido del Markdown: cada párrafo, cada frase, cada ejemplo, "
    "cada tabla completa, cada lista, cada bloque de código, cada referencia.\n"
    "2. **FRAGMENTO HTML:** Genera SOLO el contenido del <body>. "
    "NO incluyas <html>, <head>, <style>, <body> ni scripts.\n"
    "3. **HTML5 SEMÁNTICO:** Usa <section id=\"...\">, <h2>, <h3>, <h4>, "
    "<p>, <ul>, <ol>, <table>, <pre><code>.\n"
    "4. **EXTENSIÓN:** Si el Markdown es largo, tu HTML debe ser "
    "igualmente largo. No hay límite de tamaño.\n\n"
    "## ESTRUCTURA DEL DOCUMENTO (orden recomendado)\n\n"
    "Usa esta estructura general de secciones adaptándola al contenido:\n\n"
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
    "## CLASES CSS DISPONIBLES\n\n"
    "NO inventes otras clases. Usa solo:\n"
    ".subtitle, .toc, .obj-list, .intro-box, "
    ".note, .warning, .success, .hw-box, "
    ".summary-grid, .summary-card, .cmd-table, "
    ".steps, .step-num, .ref-section, .nav-links.\n\n"
    "## REGLAS DE CALIDAD\n\n"
    "- **NO AÑADAS TEXTO PROPIO:** No incluyas comentarios, "
    "resúmenes, opiniones o metadatos que no estén en el original.\n"
    "- **TABLAS COMPLETAS:** Cada fila y columna del Markdown "
    "debe estar en tu HTML.\n"
    "- **BIBLIOGRAFÍA COMPLETA:** Incluye todas las referencias al final.\n"
    "- **EJEMPLOS Y ACTIVIDADES:** Preserva cada uno con su enunciado completo.\n"
    "- **LISTAS:** Convierte listas Markdown a <ul>/<ol> con anidación correcta.\n\n"
    "## IDIOMA\n\n"
    "Respeta el idioma del contenido fuente. Si el original está "
    "en español, el HTML debe estar en español. Si en inglés, "
    "en inglés. Y así con cualquier idioma.\n\n"
    "IMPORTANTE: Devuelve SOLO el fragmento HTML. Sin Markdown, "
    "sin bloques de código, sin explicación.")


# ── Flashcards Generation (✨ Nb Flashcards, Phase 47) ───────────────

FLASHCARDS_GENERATION_PROMPT = (
    "# INSTRUCTION: GENERATE STUDY FLASHCARDS\n\n"
    "Create study flashcards (front/back pairs) from the following academic "
    "content. The flashcards are for active recall practice: the front is a "
    "concise prompt/question, the back is the complete clear explanation.\n\n"
    "## LEVEL (MAXIMUM CARDS)\n\n"
    "The user selected the `{max_cards}` level. This is a HARD MAXIMUM: "
    "generate AT MOST {max_cards} cards. You may generate fewer if the "
    "content has fewer truly essential concepts.\n\n"
    "## EXTRACTION & PRIORITIZATION\n\n"
    "1. **EXTRACT ALL key concepts** from the content: definitions, "
    "processes, distinctions, formulas, rules, best practices, and "
    "relationships between ideas.\n"
    "2. **PRIORITIZE them by importance**: essential concepts first "
    "(core definitions, must-know facts, foundational ideas), then "
    "secondary and tertiary ones.\n"
    "3. **SELECT the best {max_cards}** after prioritizing. Never pad with "
    "trivial or repeated cards — quality over quantity. Cover the essential "
    "concepts first so nothing important is left out.\n\n"
    "## OUTPUT FORMAT\n\n"
    "Return a valid JSON object. NO markdown, NO code fences, "
    "NO extra text — ONLY the JSON object:\n"
    "{{\n"
    '  "cards": [\n'
    '    {{\n'
    '      "front": "Concise concept or question",\n'
    '      "back": "Brief clear explanation"\n'
    "    }}\n"
    "  ],\n"
    '  "total_concepts": 12\n'
    "}}\n\n"
    "- `front`: a concise prompt (concept name, question, or term). "
    "Max ~12 words. One concept per card.\n"
    "- `back`: the clear, complete explanation of the front. "
    "Answer in 1-2 concise sentences (max ~25 words).\n"
    "- `total_concepts`: the total number of key concepts identified "
    "in the content (may be larger than the number of cards).\n\n"
    "## CARD QUALITY RULES\n\n"
    "1. **ONE CONCEPT PER CARD** — no compound cards.\n"
    "2. **SELF-CONTAINED BACK** — the back must be understandable "
    "without the source material.\n"
    "3. **DISTINCT** — no duplicate or near-duplicate cards.\n"
    "4. **CARDINALITY** — cards.length must be between 1 and {max_cards}.\n\n"
    "## LANGUAGE\n\n"
    "Match the source content's language. If the source is in Spanish, "
    "cards must be in Spanish. If in English, in English.\n\n"
    "CRITICAL: Return ONLY the JSON object. No other text.")
