"""
Markdown template helpers for NotebookLM prompts (Phase 62 / issue #253).

Port of v2 ``backend.ai.generation.prompts`` pieces needed by
``compose_nb_md_prompt`` (template_id/language/length overrides).

Exports:
    _lang_instruction, _length_instruction, _ROLES_BY_ID, get_md_template_role
"""



# ─── Language instruction ───────────────────────────────────

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




# ─── Length instruction ────────────────────────────────────

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




# ─── Role constants (_ROLES_BY_ID values) ───────────────────

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



# ─── Role registry ──────────────────────────────────────────

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



# ─── Template role lookup ───────────────────────────────────

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

