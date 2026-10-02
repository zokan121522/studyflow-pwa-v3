"""
Prompt builders for AI-powered generation tasks.

Each builder returns a structured prompt string that can be sent directly
to an LLM for processing.

Exports:
    _build_md_cleanup_prompt
    MD_RICH_SYSTEM_PROMPT
"""


# ═══════════════════════════════════════════════════════════════════
# Phase 53 — Rich Obsidian notes for the OpenZEN markdown button.
#
# Replaces the generic "restructuring" prompt used by
# _run_opencode_md_task() (runner.py). Inspired by _FORMAT_RICH from
# youtube_zen.py: colored callouts, emoji section headings, bolded key
# terms. Adds transcription reconstruction: the AI must interpret
# ASR/OCR noise instead of copying raw text literally.
# ═══════════════════════════════════════════════════════════════════
# Rich Obsidian output format — appended LAST to MD_RICH_SYSTEM_PROMPT
# so it is the most recent formatting instruction. Produces colored
# callouts, bold key terms and emoji section headings.
# ═══════════════════════════════════════════════════════════════════
_FORMAT_RICH_MD = (
    "OUTPUT FORMAT — RICH OBSIDIAN MARKDOWN (MANDATORY):\n"
    "- Render each required section as an emoji H2 heading, e.g. "
    "`## 📖 Definition`, `## 🔗 Key Concepts`, `## ⚙️ How It Works`, "
    "`## 💡 Practical Examples`, `## ⚠️ Common Pitfalls`, `## 🚀 Takeaways` "
    "(section names in the OUTPUT language — the language of the content).\n"
    "- Put a colored Obsidian callout right after each heading:\n"
    "  - `> [!info]` — blue facts\n"
    "  - `> [!warning]` — red/orange pitfalls or mistakes\n"
    "  - `> [!tip]` — green advice\n"
    "  - `> [!example]` — purple examples\n"
    "  Each callout: 2-4 sentences, first line a short bold label.\n"
    "  If the content lacks material for multiple callouts, use ONE "
    "informative callout per section rather than fabricating content.\n"
    "- Bold (`**term**`) every important term on FIRST use.\n"
    "- Use markdown tables for comparisons of 3+ items ONLY when the "
    "content actually compares things (do NOT force a table that invents "
    "comparison structure).\n"
    "- Use fenced code blocks (```lang) ONLY if the content mentions code.\n"
    "- If a YouTube video link is present anywhere in the content, add a "
    "top callout BEFORE the first heading: "
    "`> [!info] 🎬 Video original: [Ver en YouTube](URL)` with that URL.\n"
    "- NEVER wrap the whole note in a single H1 title; do NOT add "
    "introductions or farewells.\n"
    "**RESPOND IN THE SAME LANGUAGE AS THE ORIGINAL CONTENT.** "
    "If the content is in English, respond in English. "
    "If the content is in Spanish, respond in Spanish. "
    "If the content is in another language, respond in that language. "
    "NEVER translate the content — preserve the original language.\n"
    "- Reply ONLY with the rich Obsidian markdown, nothing else.\n"
)


MD_RICH_SYSTEM_PROMPT = (
    "You are an expert educational content creator and markdown editor. "
    "Your task is to transform the provided content into a RICH, "
    "well-organized Obsidian note that is pleasant to read and teaches "
    "the content clearly.\n\n"
    "Content type awareness:\n"
    "- If the input is a NOISY transcription (speech-to-text or OCR errors, "
    "\"scene1.py\" words, broken syntax), FIRST reconstruct it: infer the "
    "correct meaning from context and write the note in clean, correct "
    "language. NEVER copy the raw noisy text literally.\n"
    "- If the input is already structured markdown, keep the content and "
    "reorganize it into the rich format below without losing information.\n\n"
    "Required sections (adapt each to the content — if the content lacks "
    "material for a section, briefly synthesize it from the demonstrated "
    "method, or omit it rather than inventing):\n"
    "1. **Definition** — what the topic/idea is, 3-4 sentences with precise terminology\n"
    "2. **Key Concepts** — the most important ideas from the content, each explained in 1-2 sentences\n"
    "3. **How It Works** — the mechanism, process or method behind it (only if the content supports it)\n"
    "4. **Practical Examples** — examples quoted or demonstrated in the content, with the exact phrasing when it is idiomatic or memorable\n"
    "5. **Common Pitfalls** — mistakes the content warns about, or common confusions it clears up\n"
    "6. **Takeaways** — the essential ideas worth remembering, as a short bullet list\n\n"
    f"{_FORMAT_RICH_MD}"
)


def _build_md_cleanup_prompt(raw_markdown: str) -> str:
    """Build a prompt for cleaning up machine-generated markdown.

    The Tools pipeline (PyMuPDF + pdfplumber) extracts text and tables
    from PDFs, but produces two issues:
      1. **Table duplication**: Table content appears both as raw text
         (from PyMuPDF) and as pipe tables (from pdfplumber).
      2. **Broken paragraphs**: PDF column layout produces artificial
         line breaks within paragraphs.

    This prompt instructs the LLM to fix both issues while preserving
    all content and structure.

    Args:
        raw_markdown: The markdown string generated by local_converter.py.

    Returns:
        A complete prompt string for the LLM.
    """
    return f"""## Instrucciones (síguelas en orden)

1. **Une párrafos rotos**: Las líneas cortas que forman parte de un mismo párrafo deben unirse en un solo párrafo. Una línea corta es evidencia de un salto de línea artificial del PDF, no de un nuevo párrafo.

2. **Conserva las cabeceras**: No modifiques las líneas que empiezan con ## o ###. Son correctas.

3. **Conserva [Página N]**: NO elimines los marcadores [Página 1], [Página 2], etc. Son necesarios para la navegación. Solo límpialos a un formato consistente: "[Página N]" en su propia línea.

4. **NO añadas contenido nuevo**: No inventes texto, no resumas, no reescribas. Solo limpia la estructura.

5. **NO elimines contenido**: Conserva TODO el texto original. No elimines ninguna línea, tabla o párrafo.

6. **Mantén el espaciado**: Deja una línea en blanco entre secciones y entre párrafos para legibilidad.

7. **Conserva las pipe tables**: Las líneas que empiezan con | son pipe tables. CONSÉRVALAS TAL CUAL. No las modifiques ni las elimines.

## Markdown a limpiar

{raw_markdown}"""


# ═══════════════════════════════════════════════════════════════════
# Phase 54 — OpenZEN Config Modal: Prompt Template Library.
#
# The 🤖 OpenZEN button used a single fixed prompt (MD_RICH_SYSTEM_PROMPT).
# Now the user picks a TEMPLATE (10 architectures), LENGTH and LANGUAGE
# from a config modal. MD_TEMPLATES maps template_id → {name, emoji,
# description, mock (rendered as live preview in the frontend via
# window.App.ContentBlocks._renderMd), builder(language, length)}.
# ═══════════════════════════════════════════════════════════════════


def _lang_instruction(language: str) -> str:
    """Return the output-language instruction block.

    Args:
        language: 'auto' (preserve source language), 'es' or 'en'.

    Returns:
        Instruction string appended to every prompted template.
    """
    if language == "es":
        return (
            "**RESPOND IN SPANISH.** Write the whole note in Spanish regardless "
            "of the original content language. Keep technical terms in their "
            "original form when appropriate.\n"
        )
    if language == "en":
        return (
            "**RESPOND IN ENGLISH.** Write the whole note in English regardless "
            "of the original content language. Keep technical terms as-is.\n"
        )
    # auto (default) — preserve the source language
    return (
        "**RESPOND IN THE SAME LANGUAGE AS THE ORIGINAL CONTENT.** "
        "If the content is in English, respond in English. "
        "If the content is in Spanish, respond in Spanish. "
        "If the content is in another language, respond in that language. "
        "NEVER translate the content — preserve the original language.\n"
    )


def _length_instruction(length: str) -> str:
    """Return the verbosity instruction block.

    Args:
        length: 'concise', 'standard' or 'detailed'.

    Returns:
        Instruction block for the requested verbosity level. 'standard' gets
        an explicit full-development instruction (it must NOT be escueto).
    """
    if length == "concise":
        return (
            "Keep the output CONCISE: short paragraphs that still cover each "
            "key idea in full sentences, up to 2 callouts per section, and "
            "skip filler or repetition — but do NOT cut necessary explanations.\n"
        )
    if length == "standard":
        return (
            "Give the output FULL DEVELOPMENT: every section substantially "
            "explained with complete sentences and concrete references to the "
            "source, plus 2-3 callouts per section when the content supports "
            "them. Do not leave any section as a stub — expand each idea "
            "enough to stand alone.\n"
        )
    if length == "detailed":
        return (
            "Expand each section moderately: add relevant context and one or "
            "two extra examples when the content supports them, and mention "
            "edge cases only when they clarify the concept. Avoid exhaustive "
            "catalogues. Do not invent facts beyond the source.\n"
        )
    return ""


def _compose(role: str, format_block: str, language: str, length: str) -> str:
    """Assemble a full system prompt from role, format, language and length."""
    return (
        role
        + "\n\n"
        + format_block
        + "\n"
        + _lang_instruction(language)
        + _length_instruction(length)
        + "Reply ONLY with the requested markdown, nothing else.\n"
    )


# ═══════════════════════════════════════════════════════════════════
# Pure ROLES — the architecture/instruction of each template WITHOUT the
# generic format/lang/length blocks.  Single source of truth: the
# builders below compose them with _FORMAT_RICH_MD for the OpenZEN modal,
# and youtube_zen.build_yt_prompt composes them with the YT-specific
# format rules (_FORMAT_RICH / _FORMAT_RICH_POR_TEMA) for YouTube Zen.
# ═══════════════════════════════════════════════════════════════════

_ROLE_STANDARD = (
    "You are an expert educational content creator and markdown editor. "
    "Your task is to transform the provided content into a RICH, "
    "well-organized Obsidian note that is pleasant to read and teaches "
    "the content clearly.\n\n"
    "Content type awareness:\n"
    "- If the input is a NOISY transcription (speech-to-text or OCR errors, "
    "\"scene1.py\" words, broken syntax), FIRST reconstruct it: infer the "
    "correct meaning from context and write the note in clean, correct "
    "language. NEVER copy the raw noisy text literally.\n"
    "- If the input is already structured markdown, keep the content and "
    "reorganize it into the rich format below without losing information.\n\n"
    "Required sections (adapt each to the content — if the content lacks "
    "material for a section, briefly synthesize it from the demonstrated "
    "method, or omit it rather than inventing):\n"
    "1. **Definition** — what the topic/idea is, 3-4 sentences with precise terminology\n"
    "2. **Key Concepts** — the most important ideas from the content, each explained in 1-2 sentences\n"
    "3. **How It Works** — the mechanism, process or method behind it (only if the content supports it)\n"
    "4. **Practical Examples** — examples quoted or demonstrated in the content, with the exact phrasing when it is idiomatic or memorable\n"
    "5. **Common Pitfalls** — mistakes the content warns about, or common confusions it clears up\n"
    "6. **Takeaways** — the essential ideas worth remembering, as a short bullet list\n"
)

_ROLE_TRANSCRIPTION = (
    "You are a transcription restoration expert. The provided content comes "
    "from speech-to-text or OCR output and contains errors (fragmented words, "
    "homophone mistakes, broken syntax, stray tokens).\n\n"
    "Your task:\n"
    "1. Reconstruct the text: infer the correct meaning from context and "
    "write the note in clean, correct language. NEVER keep raw noisy text.\n"
    "2. Highlight notable corrections: after the reconstructed note, add a "
    "short section listing the most significant fixes (original → corrected) "
    "when they are illustrative.\n"
    "3. Organize the cleaned content into clear sections with emoji H2 "
    "headings matching the topics discussed.\n\n"
    "If the content is already clean structured markdown, simply reorganize "
    "it with the format below and skip the correction list.\n"
)

_ROLE_TUTORIAL = (
    "You are a technical tutorial writer. Convert the provided content into "
    "a clear STEP-BY-STEP guide that a learner can follow to reproduce the "
    "procedure.\n\n"
    "Structure required (adapt to content):\n"
    "1. **Overview** — what the reader will achieve.\n"
    "2. **Prerequisites / Requirements** — tools, versions, config needed.\n"
    "3. **Steps** — numbered steps, each with a short explanation and the "
    "exact command/snippet where relevant, in fenced code blocks.\n"
    "4. **Verification** — how to confirm the result worked.\n"
    "5. **Common Errors** — frequent mistakes and their fixes.\n\n"
    "Use `> [!warning]` callouts for pitfalls and `> [!tip]` for key advice.\n"
)

_ROLE_COMPARISON = (
    "You are a comparison analyst. Convert the provided content into a "
    "structured COMPARISON that helps the reader decide between options.\n\n"
    "Structure required (adapt to content):\n"
    "1. **Context** — the decision being made.\n"
    "2. **Comparison table** — a markdown table with one row per criterion "
    "and one column per option (3+ options: keep the table; 2 options: table "
    "is fine too).\n"
    "3. **Analysis** — short paragraph on the key trade-offs per option.\n"
    "4. **Recommendation** — a `> [!info]` callout with the best choice for "
    "common scenarios.\n\n"
    "Only include criteria supported by the source content; do NOT invent "
    "comparison dimensions.\n"
)

_ROLE_GLOSSARY = (
    "You are a terminology specialist. Convert the provided content into a "
    "compact GLOSSARY of the key terms and concepts it introduces.\n\n"
    "Structure:\n"
    "1. **Glossary list** — alphabetically ordered list of terms, each with "
    "`**Term** — definition` (1-2 sentences, precise).\n"
    "2. **Key abbreviations** — any acronyms/abbreviations with their "
    "expanded form, in a small table when there are 3+.\n"
    "3. **Related terms** — a `> [!info]` callout linking closely related "
    "terms.\n\n"
    "Be compact: priority is precision and coverage, not prose.\n"
)

_ROLE_EXECUTIVE = (
    "You are an executive summarizer. Distill the provided content into a "
    "SHORT, high-signal summary a busy reader can absorb in 1 minute.\n\n"
    "Structure:\n"
    "1. **One-liner** — the single most important message, as a "
    "`> [!info]` callout.\n"
    "2. **Key points** — 4-6 concise bullets, each with a bold lead word.\n"
    "3. **Risks / caveats** — a `> [!warning]` callout with the main "
    "limitations or open questions.\n"
    "4. **Relevant numbers** — any figures/metrics from the source, as a "
    "compact table when they exist.\n\n"
    "Do NOT summarize by paraphrasing every paragraph — select what matters.\n"
)

_ROLE_TOPICS = (
    "You are an information architect. Reorganize the provided content into "
    "well-separated THEMATIC SECTIONS that make the material easy to navigate "
    "and review.\n\n"
    "Structure:\n"
    "1. Group the content into 3-7 coherent themes (detect the natural "
    "grouping: by concept, by chronology, by difficulty, by use-case...).\n"
    "2. Each theme = an emoji H2 heading `## 🧩 Tema N: Name` followed by a "
    "`> [!info]` callout summarizing the theme, then the supporting content.\n"
    "3. End with a **Roadmap** section: `> [!tip]` callout suggesting the "
    "order in which to study the themes.\n\n"
    "Themes must be MUTUALLY exclusive (no overlapping content between "
    "sections) and together cover ALL source material.\n"
)

_ROLE_FAQ = (
    "You are a documentation editor specializing in FAQs. Convert the "
    "provided content into a QUESTION-ANSWER format that answers the "
    "questions a reader would realistically ask.\n\n"
    "Structure:\n"
    "1. **FAQ list** — 5-10 questions (as bold `**Question?**`), each "
    "followed by an answer callout:\n"
    "   - `> [!info]` — factual answers\n"
    "   - `> [!tip]` — how-to / advice answers\n"
    "   - `> [!warning]` — answers about pitfalls or when NOT to do something\n"
    "2. If the source states statistics or comparisons, include them in an "
    "inline table under the relevant question.\n\n"
    "Questions must derive from the source content — do not invent topics "
    "the source never mentions.\n"
)

_ROLE_ARCHITECTURE = (
    "You are a software architect documenting technical systems. Convert the "
    "provided content into a TECHNICAL ARCHITECTURE note.\n\n"
    "Structure:\n"
    "1. **Components** — the building blocks (with a `> [!info]` callout).\n"
    "2. **Data / control flow** — ASCII diagram in a fenced code block "
    "showing how components interact (arrows, labels).\n"
    "3. **Responsibilities** — what each component does, in a table "
    "(Component | Responsibility | Key detail).\n"
    "4. **Bottlenecks / risks** — a `> [!warning]` callout with the "
    "weak points.\n"
    "5. **Decisions** — any architecture trade-offs mentioned, as bullets.\n\n"
    "Only document what the source supports; if a diagram cannot be inferred "
    "from the content, describe the flow textually instead.\n"
)

_ROLE_INFOGRAPHIC = (
    "You are a visual content designer working in pure markdown. Convert the "
    "provided content into a TEXTUAL INFOGRAPHIC: maximally scannable, "
    "visually rich Obsidian output.\n\n"
    "Guidelines:\n"
    "1. Open with a bold `** clave**` assertion as the hook, then a "
    "`> [!info]` callout with the most striking data point.\n"
    "2. Use dense colored callouts (`[!info]`, `[!warning]`, `[!tip]`, "
    "`[!example]`) as visual blocks — each 2-4 sentences.\n"
    "3. Use tables for every numeric comparison or grouped list.\n"
    "4. Use emoji H2 headings with punchy names.\n"
    "5. Add a mini `> [!example]` case study when the source has one.\n\n"
    "Priority: visual rhythm and scannability over exhaustive coverage — "
    "but never omit essential facts.\n"
)

_ROLES_BY_ID: dict[str, str] = {
    "notas-estandar": _ROLE_STANDARD,
    "transcripcion": _ROLE_TRANSCRIPTION,
    "tutorial": _ROLE_TUTORIAL,
    "comparativa": _ROLE_COMPARISON,
    "glosario": _ROLE_GLOSSARY,
    "resumen-ejecutivo": _ROLE_EXECUTIVE,
    "por-temas": _ROLE_TOPICS,
    "faq": _ROLE_FAQ,
    "arquitectura-tecnica": _ROLE_ARCHITECTURE,
    "infografia-textual": _ROLE_INFOGRAPHIC,
}


def get_md_template_role(template_id: str | None) -> str | None:
    """Return the pure role/architecture instruction of a template.

    Unlike ``build_md_system_prompt`` this returns ONLY the role block
    (no generic _FORMAT_RICH_MD / language / length / "Reply ONLY"
    suffixes) so callers like youtube_zen can compose it with their own
    format rules and invariants.

    Args:
        template_id: Template id from MD_TEMPLATES (or None).

    Returns:
        The role string, or None when the template id is unknown/missing.
    """
    if not template_id:
        return None
    return _ROLES_BY_ID.get(template_id)


def _b_standard(language: str, length: str) -> str:
    """Template 1 — Notas estándar (same behaviour as MD_RICH_SYSTEM_PROMPT)."""
    return _compose(_ROLE_STANDARD, _FORMAT_RICH_MD, language, length)


def _b_transcription(language: str, length: str) -> str:
    """Template 2 — Reconstrucción de transcripción (ASR/OCR)."""
    return _compose(_ROLE_TRANSCRIPTION, _FORMAT_RICH_MD, language, length)


def _b_tutorial(language: str, length: str) -> str:
    """Template 3 — Tutorial / How-To."""
    return _compose(_ROLE_TUTORIAL, _FORMAT_RICH_MD, language, length)


def _b_comparison(language: str, length: str) -> str:
    """Template 4 — Comparativa."""
    return _compose(_ROLE_COMPARISON, _FORMAT_RICH_MD, language, length)


def _b_glossary(language: str, length: str) -> str:
    """Template 5 — Glosario / Términos."""
    return _compose(_ROLE_GLOSSARY, _FORMAT_RICH_MD, language, length)


def _b_executive(language: str, length: str) -> str:
    """Template 6 — Resumen ejecutivo."""
    return _compose(_ROLE_EXECUTIVE, _FORMAT_RICH_MD, language, length)


def _b_topics(language: str, length: str) -> str:
    """Template 7 — Por temas."""
    return _compose(_ROLE_TOPICS, _FORMAT_RICH_MD, language, length)


def _b_faq(language: str, length: str) -> str:
    """Template 8 — FAQ."""
    return _compose(_ROLE_FAQ, _FORMAT_RICH_MD, language, length)


def _b_architecture(language: str, length: str) -> str:
    """Template 9 — Arquitectura técnica."""
    return _compose(_ROLE_ARCHITECTURE, _FORMAT_RICH_MD, language, length)


def _b_infographic(language: str, length: str) -> str:
    """Template 10 — Infografía textual."""
    return _compose(_ROLE_INFOGRAPHIC, _FORMAT_RICH_MD, language, length)


MD_TEMPLATES: dict[str, dict] = {
    "notas-estandar": {
        "name": "Notas estándar",
        "emoji": "📝",
        "description": "Teoría general — callouts de color, títulos con emoji y negritas.",
        "mock": (
            "## 📖 Definición\n"
            "La **computación en la nube** ofrece recursos informáticos bajo demanda por internet.\n\n"
            "> [!info] **Concepto clave**\n"
            "> Escalado automático: la capacidad crece o se reduce según el tráfico real.\n\n"
            "## 🔗 Conceptos clave\n"
            "- **Escalabilidad** — ajustar recursos según necesidad\n"
            "- **Virtualización** — abstraer el hardware físico\n\n"
            "> [!tip] **Consejo**\n"
            "> Mide el *lock-in* del proveedor antes de elegir plataforma.\n\n"
            "## 🚀 Takeaways\n"
            "- ✅ La nube = recursos bajo demanda y pago por uso\n"
        ),
        "builder": _b_standard,
    },
    "transcripcion": {
        "name": "Reconstrucción de transcripción",
        "emoji": "🧠",
        "description": "Transcripciones ruidosas (ASR/OCR) — reconstruye, corrige y organiza.",
        "mock": (
            "## 🧠 Transcripción reconstruida\n"
            "> [!info] **Interpretación**\n"
            "> \"la mikrosirvicetos son arquitetura ke divide la aplikasion\" → Los "
            "**microservicios** son una arquitectura que divide la aplicación en servicios independientes.\n\n"
            "## ⚠️ Errores ASR corregidos\n"
            "- mikrosirvicetos → **microservicios**\n"
            "- aplikasion → **aplicación**\n\n"
            "## 📌 Idea principal\n"
            "Los microservicios permiten desplegar cada servicio de forma aislada.\n"
        ),
        "builder": _b_transcription,
    },
    "tutorial": {
        "name": "Tutorial / How-To",
        "emoji": "⚙️",
        "description": "Guías prácticas — pasos numerados, comandos y errores comunes.",
        "mock": (
            "## ⚙️ Cómo crear un contenedor Docker\n"
            "> [!tip] **Requisitos**\n"
            "> Docker instalado y el demonio en ejecución.\n\n"
            "1. Crea el `Dockerfile` en la raíz\n"
            "2. Construye la imagen: `docker build -t mi-app .`\n"
            "3. Ejecuta: `docker run -p 8080:80 mi-app`\n\n"
            "> [!warning] **Error común**\n"
            "> Olvidar `-p` hace que el puerto no sea accesible desde el host.\n\n"
            "## ✅ Verificación\n"
            "Abre `http://localhost:8080` y confirma que la app responde.\n"
        ),
        "builder": _b_tutorial,
    },
    "comparativa": {
        "name": "Comparativa",
        "emoji": "🔀",
        "description": "Contenido que compara opciones — tablas y recomendación final.",
        "mock": (
            "## 🔀 Flask vs FastAPI\n\n"
            "| Criterio | Flask | FastAPI |\n"
            "|----------|-------|---------|\n"
            "| Rendimiento | Medio | Alto |\n"
            "| Validación | Manual | Automática (Pydantic) |\n"
            "| Docs | Sin auto | OpenAPI |\n\n"
            "> [!info] **Veredicto**\n"
            "> Flask para prototipos; FastAPI para APIs con validación estricta y rendimiento.\n"
        ),
        "builder": _b_comparison,
    },
    "glosario": {
        "name": "Glosario / Términos",
        "emoji": "📚",
        "description": "Definiciones compactas, siglas y términos relacionados.",
        "mock": (
            "## 📚 Glosario — Términos clave\n"
            "> [!example] **Formato**\n"
            "> Definiciones compactas, una por término, en orden alfabético.\n\n"
            "- **API** — Interfaz de programación de aplicaciones.\n"
            "- **ORM** — Mapeo objeto-relacional entre código y base de datos.\n\n"
            "| Sigla | Significado |\n"
            "|-------|-------------|\n"
            "| API | Application Programming Interface |\n"
            "| ORM | Object-Relational Mapping |\n\n"
            "> [!info] **Términos relacionados**\n"
            "> API ↔ SDK: el SDK envuelve la API con utilidades de alto nivel.\n"
        ),
        "builder": _b_glossary,
    },
    "resumen-ejecutivo": {
        "name": "Resumen ejecutivo",
        "emoji": "🎯",
        "description": "Markdown largo → síntesis breve con bullets y cifras clave.",
        "mock": (
            "## 🎯 Resumen ejecutivo\n"
            "> [!info] **En una frase**\n"
            "> La automatización de tests reduce hasta un 40 % los bugs en producción.\n\n"
            "- ✅ **CI/CD** — integración en cada commit\n"
            "- ✅ **Cobertura** — mínimo 80 % en módulos críticos\n"
            "- ✅ **Feedback** — alertas tempranas al equipo\n\n"
            "> [!warning] **Riesgo**\n"
            "> La cobertura alta no garantiza calidad si los tests son superficiales.\n"
        ),
        "builder": _b_executive,
    },
    "por-temas": {
        "name": "Por temas",
        "emoji": "🧩",
        "description": "Reorganiza en secciones temáticas mutuamente excluyentes.",
        "mock": (
            "## 🧩 Tema 1: Fundamentos\n"
            "> [!info] **Ideas clave**\n"
            "> La base del tema: definiciones y principios esenciales.\n\n"
            "## 🧩 Tema 2: Aplicaciones\n"
            "> [!example] **Casos de uso**\n"
            "> Dónde y cómo se aplica en el mundo real.\n\n"
            "## 🧩 Tema 3: Limitaciones\n"
            "> [!warning] **Cuándo NO usarlo**\n"
            "> Contextos donde la técnica falla o no aporta valor.\n"
        ),
        "builder": _b_topics,
    },
    "faq": {
        "name": "FAQ",
        "emoji": "❓",
        "description": "Convierte el contenido en preguntas y respuestas claras.",
        "mock": (
            "## ❓ Preguntas frecuentes\n\n"
            "**¿Qué es X?**\n"
            "> [!info] **Respuesta**\n"
            "> Definición breve y clara.\n\n"
            "**¿Cómo se instala?**\n"
            "> [!tip] **Respuesta**\n"
            "> Pasos rápidos con el comando principal.\n\n"
            "**¿Cuándo evitar X?**\n"
            "> [!warning] **Respuesta**\n"
            "> Casos donde X no es la mejor opción.\n"
        ),
        "builder": _b_faq,
    },
    "arquitectura-tecnica": {
        "name": "Arquitectura técnica",
        "emoji": "🏗️",
        "description": "Docs técnicas — componentes, flujos, diagramas ASCII y riesgos.",
        "mock": (
            "## 🏗️ Arquitectura del sistema\n"
            "> [!info] **Componentes**\n"
            "> Cliente SPA → API Flask → PostgreSQL.\n\n"
            "```\n"
            "Cliente → [Nginx] → [Flask API] → [PostgreSQL]\n"
            "                ↘ [OpenZEN sidecar]\n"
            "```\n\n"
            "> [!warning] **Cuello de botella**\n"
            "> La API sin caché satura la BD bajo picos de carga.\n\n"
            "## 🔄 Flujo de datos\n"
            "1. El cliente pide `/api/courses`\n"
            "2. Flask consulta la BD\n"
            "3. La respuesta se cachea 60 s\n"
        ),
        "builder": _b_architecture,
    },
    "infografia-textual": {
        "name": "Infografía textual",
        "emoji": "📊",
        "description": "Máxima riqueza visual — callouts densos, tablas y ritmo visual.",
        "mock": (
            "## 📊 Infografía — En cifras\n"
            "> [!info] **Dato clave**\n"
            "> 8 de cada 10 equipos usan CI/CD en 2026.\n\n"
            "| Métrica | Valor |\n"
            "|---------|-------|\n"
            "| Cambios/día | 15 |\n"
            "| Deploy time | 4 min |\n"
            "| Rollback | 30 s |\n\n"
            "> [!example] **Mini-caso**\n"
            "> El equipo redujo el deploy de 40 min a 4 min con pipelines.\n"
        ),
        "builder": _b_infographic,
    },
}


def list_md_templates() -> list[dict]:
    """Public metadata for the frontend config modal (NO internal prompts)."""
    return [
        {
            "id": tpl_id,
            "name": data["name"],
            "emoji": data["emoji"],
            "description": data["description"],
            "mock": data["mock"],
        }
        for tpl_id, data in MD_TEMPLATES.items()
    ]


def build_md_system_prompt(
    template_id: str | None = None,
    language: str = "auto",
    length: str = "standard",
) -> str:
    """Compose the system prompt for an OpenZEN markdown task.

    Args:
        template_id: One of the MD_TEMPLATES keys. Unknown/None → 'notas-estandar'.
        language: 'auto' (preserve source), 'es' or 'en'.
        length: 'concise', 'standard' or 'detailed'.

    Returns:
        The full system prompt string.
    """
    tid = template_id or "notas-estandar"
    template = MD_TEMPLATES.get(tid) or MD_TEMPLATES["notas-estandar"]
    return template["builder"](language, length)



