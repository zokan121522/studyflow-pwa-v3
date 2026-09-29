"""
NotebookLM provider — AI chat via Google NotebookLM (Gemini).

Uses the ``notebooklm`` Python SDK with cookie-based auth (no API key).
Tool calling is implemented via structured prompt engineering: the
personality system prompt instructs the model to output JSON tool
blocks, and this provider parses them from the response.

Auth:
    Same Chrome-cookie-based auth as the NotebookLM PDF pipeline — login
    via Playwright stores cookies in ~/.notebooklm/ (see login sub-phase).

v3 changes from v2: flat imports only. Behaviour is identical.
"""

import json
import logging
import re
from typing import Generator

from ai.provider import AIProvider
from ai.notebooklm.client import nb_chat
from ai.personalities import get_personality

logger = logging.getLogger(__name__)


# ─── Constants ──────────────────────────────────────────────────────

_TOOL_BLOCK_RE = re.compile(
    r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}',
    re.DOTALL,
)


# ─── Provider ───────────────────────────────────────────────────────


class NotebookLMProvider(AIProvider):
    """AI provider that uses NotebookLM (Gemini) for chat.

    Authentication is via Chrome cookies — no API key needed.
    Tool calling uses prompt engineering (model outputs JSON tool blocks).
    """

    def __init__(self, **kwargs):
        """Initialize the provider.

        Args:
            **kwargs: Ignored — NotebookLM uses a fixed Gemini model
                      behind the scenes (no model selection needed).
                      Accepting kwargs allows ``get_provider(provider_name, model=...)``
                      to work without knowing which provider accepts which args.
        """
        super().__init__()
        if kwargs:
            logger.debug("NotebookLMProvider ignored unexpected kwargs: %s", kwargs)

    @property
    def name(self) -> str:
        return "notebooklm"

    @property
    def description(self) -> str:
        return "NotebookLM (Gemini via Google account)"

    # ── Chat ──────────────────────────────────────────────────────

    def chat(self, messages: list[dict],
             tools: list[dict] | None = None,
             personality: str | None = None) -> dict:
        """Non-streaming chat via NotebookLM.

        The system prompt (from personality) is injected together with
        the conversation history into a single NotebookLM chat query.

        Args:
            messages: Conversation history (system, user, assistant, tool).
            tools: Ignored for NotebookLM (tool calling is prompt-based).
            personality: Personality name (``"alvaro"``, ``"elvira"``, etc.).

        Returns:
            Response dict with ``role``, ``content``, and optionally
            ``tool_calls`` parsed from the text response.
        """
        system_prompt, user_messages = self._split_system(messages)
        try:
            reply = nb_chat(system_prompt, user_messages)
        except RuntimeError as e:
            logger.error("NotebookLM chat error: %s", e)
            raise

        # Check for tool calls in the response text
        tool_calls = self._parse_tool_calls(reply)
        if tool_calls:
            # Remove the JSON tool block from content for clean display
            clean_content = _TOOL_BLOCK_RE.sub("", reply).strip()
            return {"role": "assistant", "content": clean_content, "tool_calls": tool_calls}

        return {"role": "assistant", "content": reply}

    # ── Stream ────────────────────────────────────────────────────

    def chat_stream(self, messages: list[dict],
                    tools: list[dict] | None = None,
                    personality: str | None = None) -> Generator[str, None, dict]:
        """Streaming chat via NotebookLM.

        Note: NotebookLM's SDK doesn't support true streaming. The full
        response is generated then yielded as a single token for API
        compatibility with the streaming interface.
        """
        system_prompt, user_messages = self._split_system(messages)
        try:
            reply = nb_chat(system_prompt, user_messages)
        except RuntimeError as e:
            logger.error("NotebookLM chat error: %s", e)
            raise

        # Yield the full response as one token
        if reply:
            yield reply

        # Check for tool calls
        tool_calls = self._parse_tool_calls(reply)
        if tool_calls:
            clean_content = _TOOL_BLOCK_RE.sub("", reply).strip()
            return {"role": "assistant", "content": clean_content, "tool_calls": tool_calls}

        return {"role": "assistant", "content": reply}

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
        """Extract the system message from the message list.

        Returns:
            (system_prompt, non_system_messages)
        """
        system_text = ""
        others: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system_text += (m.get("content", "") or "") + "\n"
            else:
                others.append(m)
        return system_text.strip(), others

    @staticmethod
    def _parse_tool_calls(text: str) -> list[dict] | None:
        """Parse JSON tool call blocks from model response text.

        Looks for patterns like::

            {"tool": "tool_name", "arguments": {"arg1": "val1"}}

        Returns:
            A list of tool call dicts in the standard format
            (``{"function": {"name": "...", "arguments": {...}}}``),
            or None if no tool calls found.
        """
        matches = _TOOL_BLOCK_RE.findall(text)
        if not matches:
            return None

        tool_calls: list[dict] = []
        for name, args_str in matches:
            try:
                args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({
                "function": {
                    "name": name,
                    "arguments": args,
                }
            })

        return tool_calls if tool_calls else None