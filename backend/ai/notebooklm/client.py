"""
NotebookLM client wrapper.

Wraps the async ``notebooklm.NotebookLMClient`` in synchronous functions
suitable for Flask background threads.

Auth:
    Cookies (Playwright ``storage_state.json``) are managed via the web UI.
    Uploaded profiles live in ``~/.notebooklm/{email}/``. The active
    profile is tracked in ``~/.notebooklm/active.txt``.

    Use ``NotebookLMClient.from_storage(profile=<email>)`` to use a specific
    profile, or ``from_storage()`` for the legacy global cookie file.

v3 changes from v2:
    - Cookie dir is ``~/.notebooklm`` (local macOS) instead of container
      ``/root/.notebooklm``; ``NOTEBOOKLM_DIR`` env overrides it.
    - Flat imports (`from ai.notebooklm.prompts import ...`).

Exports:
    nb_pdf_to_markdown, nb_pdf_to_html, nb_chat
"""

import asyncio
import logging
import os
import re
import time as _time  # noqa: F401  (kept for parity with v2 history)

from notebooklm import NotebookLMClient
from notebooklm.exceptions import ChatResponseParseError

from ai.notebooklm.prompts import (
    PDF_TO_MARKDOWN_PROMPT,
    PDF_TO_HTML_PROMPT,
)

logger = logging.getLogger(__name__)

# ── helpers ─────────────────────────────────────────────────────────

_NOTEBOOK_NAME = "tmp-phase24"
_CHAT_POLL_INTERVAL = 0.5  # seconds
_CHAT_TIMEOUT = 300  # max seconds to wait for PDF source processing (free tier can be slow)
_SOURCE_WAIT_TIMEOUT = 300.0  # max seconds to wait for NotebookLM to process a PDF source
_COOKIE_DIR = os.environ.get(
    "NOTEBOOKLM_DIR",
    os.path.join(os.path.expanduser("~"), ".notebooklm"),
)
_ACTIVE_FILE = os.path.join(_COOKIE_DIR, "active.txt")


# NotebookLM appends source-citation markers like [1], [2,3], [1-2]
# referencing its own sources UI — meaningless once the answer becomes a
# StudyFlow block. Strip at the SDK boundary so every consumer receives
# clean markdown.
_CITATION_RE = re.compile(r"\[\d+(?:\s*[,;\-–]\s*\d+)*\]")
# A line counts as a fenced-code delimiter iff it is *only* 3+ backticks
# (optionally followed by a language tag and whitespace). This rejects
# inline ````foo```` snippets while matching ```` ```python ``, ```` ``` ``,
# and standard openers/closers.
_FENCE_LINE_RE = re.compile(r"^[ \t]*`{3,}[^\n`]*$")


def _strip_citation_markers(text: str) -> str:
    """Strip citation markers + whitespace artefacts, fence-aware.

    The pipeline (citation strip → space-before-punctuation collapse → 2+
    space collapse → leading/trailing space trim) is applied per-line ONLY
    outside triple-backtick fences. Fenced regions — the opener/closer
    lines and everything in between — are emitted byte-for-byte to
    preserve NB code indentation and bracket-indexed access like
    ``a[1]``.
    """
    if not isinstance(text, str) or not text:
        return text
    out, in_fence = [], False
    for line in text.splitlines(keepends=True):
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)  # preserve code verbatim
            continue
        line = _CITATION_RE.sub("", line)
        line = re.sub(r" +([,.;:!?])", r"\1", line)
        line = re.sub(r" {2,}", " ", line)
        line = re.sub(r"^ +", "", line)
        line = re.sub(r" +$", "", line, flags=re.MULTILINE)
        out.append(line)
    return "".join(out)


def _sync_run(coro):
    """Run a coroutine synchronously (blocks until done)."""
    return asyncio.run(coro)


from ai.notebooklm.utils import get_active_profile as _get_active_profile  # noqa: E402


async def _chat_ask(client, nb_id, prompt):
    """Ask a question to the notebook and return the answer text.

    Uses the Chat API (50 queries/day).
    """
    result = await client.chat.ask(nb_id, prompt)
    return _strip_citation_markers(result.answer)


async def _add_file_and_ask(pdf_path: str, prompt: str) -> str:
    """One-shot pipeline: create notebook → add PDF → ask → delete notebook.

    Args:
        pdf_path: Absolute path to the PDF file on disk.
        prompt: The question/prompt to ask the notebook.

    Returns:
        The answer text from NotebookLM.

    Raises:
        RuntimeError: On auth failure, upload failure, or timeout.
    """
    profile = _get_active_profile()
    kwargs = {"profile": profile} if profile else {}
    async with NotebookLMClient.from_storage(**kwargs) as client:
        # 1. Create temporary notebook
        nb = await client.notebooks.create(_NOTEBOOK_NAME)

        try:
            # 2. Add PDF source and wait for processing
            source = await client.sources.add_file(
                nb.id, pdf_path, wait=True, wait_timeout=_SOURCE_WAIT_TIMEOUT
            )
            if not source or not source.id:
                raise RuntimeError("Failed to add PDF source to NotebookLM")

            # 3. Ask the question (chat API)
            answer = await client.chat.ask(nb.id, prompt)
            if not answer or not answer.answer:
                raise RuntimeError("NotebookLM returned empty answer")

            return _strip_citation_markers(answer.answer)

        finally:
            # 4. Always clean up the temporary notebook
            try:
                await client.notebooks.delete(nb.id)
            except Exception:
                pass  # best-effort cleanup


# ── General chat ────────────────────────────────────────────────────

_CHAT_NOTEBOOK_NAME = "studyflow-chat"
_CHAT_GENERAL_TIMEOUT = 120  # seconds


async def _chat_general(system_prompt: str,
                        messages: list[dict]) -> str:
    """General-purpose chat via NotebookLM (no PDF needed).

    Creates a temporary notebook, injects the system prompt + full
    conversation history as context, and returns the answer.

    The notebook is deleted after each call. Conversation history is
    maintained on our side (chat_messages DB table) and injected into
    every prompt so the model sees the full context.

    Retries ONCE on ``ChatResponseParseError`` (transient API glitch or
    wire-format drift) with a fresh notebook before giving up.

    Args:
        system_prompt: Personality / behaviour instructions.
        messages: List of ``{"role": ..., "content": ...}`` dicts
            representing the conversation so far.

    Returns:
        The answer text from NotebookLM.

    Raises:
        RuntimeError: On auth failure, API error, or timeout.
    """
    profile = _get_active_profile()
    kwargs = {"profile": profile} if profile else {}
    async with NotebookLMClient.from_storage(**kwargs) as client:

        async def _do_ask() -> str:
            """Create notebook → ask → return answer text."""
            nb = await client.notebooks.create(_CHAT_NOTEBOOK_NAME)
            try:
                # Build context: system prompt + conversation history
                context_parts = [f"SYSTEM INSTRUCTIONS:\n{system_prompt}"]
                for msg in messages:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if not content:
                        continue
                    context_parts.append(f"{role.upper()}: {content}")

                # The last message is the current user query — tag it explicitly
                full_prompt = "\n\n".join(context_parts)
                full_prompt += "\n\nASSISTANT:"

                answer = await client.chat.ask(nb.id, full_prompt)
                if not answer or not answer.answer:
                    raise RuntimeError("NotebookLM returned empty answer")

                return _strip_citation_markers(answer.answer)
            finally:
                try:
                    await client.notebooks.delete(nb.id)
                except Exception:
                    pass  # best-effort cleanup

        # ── Attempt 1 ──────────────────────────────────────────────
        try:
            return await _do_ask()
        except ChatResponseParseError as e:
            logger.warning(
                "NotebookLM ChatResponseParseError on first attempt: %s. "
                "Retrying once with fresh notebook...",
                e,
            )
            # ── Attempt 2 (retry) ──────────────────────────────────
            try:
                return await _do_ask()
            except ChatResponseParseError as e2:
                raise RuntimeError(
                    "NotebookLM API returned an unparseable response "
                    "after 2 attempts. The API wire format may have "
                    "changed, or the session may need re-authentication."
                ) from e2


# ── public API ──────────────────────────────────────────────────────


def nb_chat(system_prompt: str,
            messages: list[dict]) -> str:
    """Synchronous wrapper for ``_chat_general``.

    Args:
        system_prompt: Personality / behaviour instructions.
        messages: Conversation history (list of role/content dicts).

    Returns:
        The answer text from NotebookLM.

    Raises:
        RuntimeError: On any failure.
    """
    return _sync_run(_chat_general(system_prompt, messages))


def nb_pdf_to_markdown(
    pdf_path: str,
    *,
    template_id: str | None = None,
    language: str = "auto",
    length: str = "standard",
) -> str:
    """PDF → Markdown via NotebookLM Chat API.

    Uploads the PDF, asks Gemini to convert it to clean, structured
    markdown, and returns the result.

    Accepts optional ``template_id``/``language``/``length`` so the shared
    markdown config modal can steer the prompt. Defaults keep the base
    behaviour exactly (base prompt unchanged).

    Args:
        pdf_path: Absolute path to the PDF file.
        template_id: MD_TEMPLATES key; None → base prompt only.
        language: 'auto' | 'es' | 'en'.
        length: 'concise' | 'standard' | 'detailed'.

    Returns:
        Markdown string.

    Raises:
        RuntimeError: On any failure.
    """
    from ai.notebooklm.prompts import compose_nb_md_prompt

    prompt = compose_nb_md_prompt(
        template_id, language, length, PDF_TO_MARKDOWN_PROMPT,
    )
    return _sync_run(_add_file_and_ask(pdf_path, prompt))


def nb_pdf_to_html(pdf_path: str) -> str:
    """PDF → HTML via NotebookLM Chat API.

    Uploads the PDF, asks Gemini to generate an HTML page with the
    content, and returns the HTML.

    Args:
        pdf_path: Absolute path to the PDF file.

    Returns:
        HTML string (body content only, no <html>/<head>/<body> wrappers).

    Raises:
        RuntimeError: On any failure.
    """
    return _sync_run(_add_file_and_ask(pdf_path, PDF_TO_HTML_PROMPT))