"""
AI Provider abstraction — abstract interface for AI chat backends.

Defines the ``AIProvider`` base class that all providers (NotebookLM, and
later OpenZEN) must implement. The agent chat system uses this interface
so it can switch between providers at runtime without changing the chat
logic.

Provider interface:
    chat(messages, tools, personality) → response dict
    chat_stream(messages, tools, personality) → yields tokens, returns final message dict

Each provider is responsible for:
    1. Building the provider-specific payload (NotebookLM chat.ask, etc.)
    2. Handling tool calling (native or prompt-based)
    3. Streaming tokens back to the caller
"""

from abc import ABC, abstractmethod
from typing import Generator


class AIProvider(ABC):
    """Abstract base for an AI chat provider.

    Every provider implements chat (non-streaming) and chat_stream (streaming).
    The caller handles the tool loop — the provider only needs to return
    response messages, optionally with tool_calls.
    """

    @abstractmethod
    def chat(self, messages: list[dict],
             tools: list[dict] | None = None,
             personality: str | None = None) -> dict:
        """Non-streaming chat completion.

        Args:
            messages: List of ``{"role": ..., "content": ...}`` dicts.
                May include ``"system"``, ``"user"``, ``"assistant"``, ``"tool"`` roles.
            tools: Optional list of tool definitions (OpenAI-compatible format).
            personality: Optional personality name (e.g. ``"alvaro"``, ``"elvira"``).
                The provider may use this to select a system prompt or voice.

        Returns:
            A response message dict with:
                - ``"role"``: ``"assistant"``
                - ``"content"``: The response text (str)
                - ``"tool_calls"``: Optional list of tool call dicts (if the model
                  wants to execute tools). Each tool call has:
                    ``{"function": {"name": "...", "arguments": {...}}}``

        Raises:
            RuntimeError: On connection, auth, or API errors.
        """
        ...

    @abstractmethod
    def chat_stream(self, messages: list[dict],
                    tools: list[dict] | None = None,
                    personality: str | None = None) -> Generator[str, None, dict]:
        """Streaming chat completion.

        Args:
            Same as ``chat()``.

        Yields:
            str: Each content token as generated.

        Returns:
            The final response message dict (same format as ``chat()``).

        Raises:
            RuntimeError: On connection, auth, or API errors.
        """
        ...  # pragma: no cover — abstract generator

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name, e.g. ``"notebooklm"``."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description for the UI, e.g. ``"NotebookLM (Gemini via Google account)"``."""
        ...