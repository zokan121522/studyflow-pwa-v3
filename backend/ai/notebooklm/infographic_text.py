"""
Infographic text-processing helpers (v3 split of ai/notebooklm/infographic.py).

Pure text transformations used by the infographic pipeline: markdown→narrative
plain text, truncation with per-language ceilings, and language detection.

No DB access, no asyncio — keeps the text helpers unit-testable.
"""

import os
import re

def _strip_markdown(text: str) -> str:
    """Convert markdown to narrative plain text for NotebookLM infographic upload.

    Problem: Even after stripping markdown syntax, Gemini interprets structured
    text (headers, bullets, tables) as 'a document to analyze' rather than
    'content to visualize'. The infographic shows structural analysis instead
    of teaching content.

    Solution: NARRATIVIZE — convert structured content into flowing paragraphs
    of natural language. No headers, no bullets, no tables. Just prose.
    """
    import re

    if not text:
        return text

    # ── Phase 1: Strip markdown syntax ──────────────────────────────
    # (same as before, but we continue to Phase 2)

    lines = text.split("\n")
    cleaned = []

    for line in lines:
        # Callouts: > [!info] content → content
        m = re.match(r"^>\s*\[!?\w*\]\s*(.*)", line)
        if m:
            line = m.group(1)
        elif line.startswith("> "):
            line = line[2:]

        # Headers: ## Title → Title (we'll join with space later)
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            line = m.group(1)

        # Table rows: | col1 | col2 | → col1: col2
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells and not all(re.match(r"^[-:]+$", c) for c in cells):
                line = " — ".join(cells)
            else:
                continue

        # Horizontal rules → skip
        if re.match(r"^[-*_]{3,}\s*$", line.strip()):
            continue

        # Strip bold/italic
        line = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"___(.+?)___", r"\1", line)
        line = re.sub(r"__(.+?)__", r"\1", line)
        line = re.sub(r"_(.+?)_", r"\1", line)

        # Strip links/images
        line = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)

        # Strip inline code and HTML
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"<[^>]+>", "", line)

        # Strip list markers
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1", line)
        line = re.sub(r"^(\s*)\d+\.\s+", r"\1", line)

        cleaned.append(line.rstrip())

    # ── Phase 2: Narrativize — flatten structure into prose ──────────
    # Join all lines, collapse whitespace, produce flowing text.
    # This prevents Gemini from treating the content as 'a structured
    # document' and instead sees it as 'teaching material to visualize'.

    full = " ".join(cleaned)

    # Collapse multiple spaces
    full = re.sub(r" {2,}", " ", full)

    # Collapse multiple newlines into single newlines (paragraph breaks)
    full = re.sub(r"\n{2,}", "\n", full)

    # Remove remaining structural markers that might confuse Gemini:
    # Emoji-only lines (📊, 📝, 🔑, etc.) that were section dividers
    full = re.sub(r"\n[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50]\s*\n", "\n", full)

    return full.strip()



def _truncate_content(text: str, max_chars: int) -> str:
    """Trim content to at most ``max_chars`` at a safe sentence boundary.

    NotebookLM infographics hang forever ("pending") when the source exceeds
    roughly 4-5k chars. We keep the most informative head of the content and
    cut at the last sentence boundary within the limit, never mid-word.
    """
    if len(text) <= max_chars:
        return text

    head = text[:max_chars]
    # Cut at the last sentence/punctuation boundary in the trimmed head so we
    # don't leave a dangling "concept something" — prefer . ! ? then newline.
    boundary = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    cut = head.rfind("\n")
    candidates = [b for b in (boundary, cut) if b > max_chars * 0.6]
    if candidates:
        pos = max(candidates)
    elif boundary > 0:
        pos = boundary
    else:
        # No good boundary: fall back to last space (avoid mid-word split)
        pos = head.rfind(" ")

    out = head[:pos + 1].rstrip()
    if not out:
        out = head.rstrip()
    return out + "\n[…] (contenido recortado para el límite de la infografía)"


# ── config ──────────────────────────────────────────────────────────



_MAX_INFOGRAPHIC_CHARS = int(os.environ.get("INFOGRAPHIC_MAX_CHARS", "3800"))
_MAX_INFOGRAPHIC_EN_CHARS = int(os.environ.get("INFOGRAPHIC_MAX_EN_CHARS", "2900"))



def _infographic_max_chars(target_lang: str) -> int:
    """Return the truncation ceiling for a target infographic language.

    English has a lower NotebookLM size ceiling than Spanish (see module
    docstring), so English content is trimmed harder to guarantee the
    generation completes in English instead of failing and falling back.
    """
    return _MAX_INFOGRAPHIC_EN_CHARS if target_lang == "en" else _MAX_INFOGRAPHIC_CHARS



def _truncate_target_lang(requested_lang: str | None, content: str) -> str:
    """Resolve the language the truncation ceiling should target.

    Mirrors the run-time language resolution: explicit request param wins,
    otherwise auto-detect from content, defaulting to Spanish. Used only to
    pick the right size ceiling (English truncates tighter).
    """
    if requested_lang and requested_lang != "auto":
        return requested_lang
    if content:
        return _detect_language(content)
    return "es"



def _sanitize_for_notebooklm(text: str) -> str:
    """Convert markdown to clean plain text that NotebookLM understands.

    NotebookLM confuses markdown formatting (callouts, tables, bold markers)
    with document structure, producing nonsensical infographics. Converting
    to plain text forces it to focus on the actual content.
    """
    import re
    # 1. Remove Obsidian callout markers: > [!type] → plain blockquote
    text = re.sub(r'>\s*\[!(?:info|tip|warning|example|note|caution|danger|bug|quote|success|question|failure|todo)\]\s*', '', text)
    # 2. Remove 4-byte emojis
    text = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE00-\uFE0F\u200D\u20E3\uE0020-\uE007F]', '', text)
    # 3. Convert markdown tables to plain text rows
    def _table_to_text(m):
        rows = [r.strip() for r in m.group(0).strip().split('\n') if r.strip()]
        # Skip separator rows (|---|---|)
        rows = [r for r in rows if not re.match(r'^[\s|:-]+$', r)]
        result = []
        for row in rows:
            cells = [c.strip() for c in row.split('|') if c.strip()]
            if cells:
                result.append(' — '.join(cells))
        return '\n'.join(result)
    text = re.sub(r'(?:^\|.+\|$\n?)+', _table_to_text, text, flags=re.MULTILINE)
    # 4. Remove markdown formatting but keep the text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # bold
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # italic
    text = re.sub(r'`(.+?)`', r'\1', text)           # inline code
    text = re.sub(r'~~(.+?)~~', r'\1', text)         # strikethrough
    # 5. Collapse YouTube URLs
    text = re.sub(r'https?://(?:youtu\.be|www\.youtube\.com)/[^\s\)]+', '[video]', text)
    # 6. Collapse other URLs
    text = re.sub(r'https?://[^\s\)]+', '', text)
    # 7. Convert blockquotes to plain text (remove leading >)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # 8. Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def _detect_language(text: str) -> str:
    """Detect the dominant language of a text snippet.

    Uses a simple heuristic: counts common English words vs Spanish words.
    Returns 'en' or 'es' (default).
    """
    if not text:
        return "es"

    text_lower = text.lower()
    # Common English words (high frequency)
    en_words = ["the", "is", "are", "was", "were", "have", "has", "had",
                "do", "does", "did", "will", "would", "could", "should",
                "can", "may", "might", "shall", "this", "that", "these",
                "those", "with", "from", "for", "about", "into", "through",
                "and", "but", "or", "not", "no", "yes", "what", "how",
                "when", "where", "why", "who", "which", "there", "here"]
    # Common Spanish words (high frequency)
    es_words = ["el", "la", "los", "las", "un", "una", "unos", "unas",
                "es", "son", "está", "están", "era", "fue", "ha", "hay",
                "que", "de", "en", "por", "con", "para", "sin", "sobre",
                "se", "lo", "le", "me", "te", "nos", "les", "al", "del",
                "y", "o", "pero", "sino", "como", "más", "menos", "muy",
                "este", "esta", "estos", "estas", "ese", "esa", "aquel"]

    words = text_lower.split()
    en_count = sum(1 for w in words if w in en_words)
    es_count = sum(1 for w in words if w in es_words)

    return "en" if en_count > es_count else "es"
