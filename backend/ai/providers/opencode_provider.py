"""
OpenCode provider — AI chat via openZEN (OpenCode ecosystem).

Connects to a local ``opencode serve`` sidecar via **raw HTTP**
(not the SDK), using a **persistent session** that lives across
tool rounds.  This is critical: the opencode server manages
conversation history server-side, so the model sees every turn as
a separate message rather than a huge embedded blob of text.

Architecture::

    studyflow-hub (Flask) ──→ opencode sidecar (port 54321)
         │                          │
         │  raw HTTP (httpx)        │  routes to local model
         │  persistent session      │  or external provider
         └──────────────────────────┘

Session lifecycle:
    One ``OpenCodeProvider`` instance = one opencode session.
    Each ``chat()`` call sends one ``POST /session/{id}/message``
    to the same session.  The first message in a session embeds
    the system prompt + tool definitions + first user query.
    Subsequent messages carry only the *delta* (tool result +
    next user turn).

Auth:
    For local models (e.g. ``big-pickle``), no API keys are needed.
    Remote providers are configured via the opencode web UI
    at ``http://localhost:54321``.
"""

import base64
import json
import logging
import os
import re
import threading
from typing import Generator

import httpx

from ai.config import (
    _get_ai_config,
    _save_ai_config,
    _invalidate_ai_config_cache,
)
from ai.provider import AIProvider

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────

# URL can be overridden via env (e.g. point OpenZEN at the Señor's Mac
# `opencode serve` on Tailscale instead of the sidecar container).
_DEFAULT_OPCODE_URL = os.environ.get("OPENCODE_SERVER_URL", "http://opencode:54321")
# Two distinct ids that used to be conflated into one attribute:
#   * PROVIDER_ID is the id the *opencode server* knows its provider by. It
#     goes on the wire as "providerID", and the server only accepts "opencode"
#     here. Sending anything else is silently ignored and the server falls back
#     to the `model` in its own opencode.jsonc.
#   * CONFIG_KEY is the id studyflow stores API keys under in ai_config, and
#     the name this provider is registered as. v2 sends CONFIG_KEY on the
#     wire, which is why the requested model never took effect.
_DEFAULT_PROVIDER_ID = "opencode"
_DEFAULT_CONFIG_KEY = "opencode-acp"
_DEFAULT_MODEL = "big-pickle"

# SSE read timeout for streaming responses (Phase 59, issue #228).
# If NO event arrives over the stream for this long, the provider is
# stalled and we cut the connection so callers (YouTube Zen chunks,
# knowledge pipeline) surface a recoverable error instead of leaving
# their task stuck in 'processing' forever. The shared client keeps its
# 900s timeout for long POST rewrites (e.g. 20-minute audio scripts);
# only the streaming read is bounded here.
_SSE_READ_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_SSE_READ_TIMEOUT", "300"))

# Regex to detect quota / auth errors in SDK or HTTP error messages
_QUOTA_PATTERNS = re.compile(
    r"(429|401|quota|rate.limit|insufficient_quota|"
    r"credit.limit|exceeded|too.many.requests|unauthorized)",
    re.IGNORECASE,
)


# ─── Exceptions ─────────────────────────────────────────────────────


class QuotaError(RuntimeError):
    """Raised when the current API key has been rate-limited or exhausted."""


# ─── Provider ───────────────────────────────────────────────────────


class OpenCodeProvider(AIProvider):
    """AI provider that uses openZEN (OpenCode ecosystem) for chat.

    Connects to a local ``opencode serve`` sidecar via raw HTTP.
    Each *instance* owns one persistent session on the opencode server,
    so multi-turn conversations work naturally — the server keeps the
    history.

    Tool calling is prompt-based: the model outputs JSON blocks like
    ``{"tool": "name", "arguments": {...}}`` inline in its text
    response.
    """

    def __init__(self, **kwargs):
        """Initialize the provider.

        Args:
            **kwargs: Optional overrides:
                server_url: OpenCode server URL (default ``http://opencode:54321``).
                provider_id: Provider ID on the opencode server (default ``opencode``).
                model: Model name to use (default ``big-pickle``).
                api_keys: Optional list of API keys (overrides config).
                key_index: Optional starting key index (overrides config).
        """
        super().__init__()
        self.server_url = kwargs.get("server_url", _DEFAULT_OPCODE_URL)
        self.provider_id = kwargs.get("provider_id", _DEFAULT_PROVIDER_ID)
        self.config_key = kwargs.get("config_key", _DEFAULT_CONFIG_KEY)
        self.model = kwargs.get("model", _DEFAULT_MODEL)

        # Basic auth for password-protected servers (e.g. Señor's Mac
        # `opencode serve` on Tailscale). Empty envs → no auth header,
        # preserving the local sidecar behavior.
        _mac_user = os.environ.get("OPENCODE_SERVER_USERNAME") or ""
        _mac_pass = os.environ.get("OPENCODE_SERVER_PASSWORD") or ""
        self._basic_auth: str | None = (
            "Basic " + base64.b64encode(f"{_mac_user}:{_mac_pass}".encode()).decode()
            if _mac_user and _mac_pass
            else None
        )

        # Resolve API keys from kwargs or persisted config, keyed by the
        # studyflow config id rather than the server's provider id.
        cfg = _get_ai_config()
        self.api_keys: list[str] = (
            kwargs.get("api_keys")
            or cfg.get("api_keys", {}).get(self.config_key, [])
        )
        self.key_index: int = kwargs.get("key_index", cfg.get("key_index", 0))

        if not self.api_keys:
            logger.info(
                "No API keys configured for provider '%s'. "
                "Local models do not require API keys.",
                self.provider_id,
            )

        # ── Persistent HTTP session (one per provider instance) ─────
        self._http: httpx.Client | None = None
        self._session_id: str | None = None
        # Track whether we already sent the "first message" (system +
        # tools) to this session.
        self._first_sent: bool = False
        # Last usage/cost reported by the opencode server (per chat call).
        self._last_usage: dict | None = None

    # ── Public properties ─────────────────────────────────────────

    @property
    def name(self) -> str:
        return "opencode-acp"

    @property
    def description(self) -> str:
        num_keys = len(self.api_keys)
        if not num_keys:
            # Local models need no key. Reporting "active #1" with zero keys
            # reads like a broken rotation, so say what is actually true.
            return f"openZEN ({self.provider_id}/{self.model}) [local, no API key]"
        active = self.key_index % num_keys
        return (
            f"openZEN ({self.provider_id}/{self.model}) "
            f"[{num_keys} key(s), active #{active + 1}]"
        )

    # ── Chat (non-streaming) ──────────────────────────────────────

    def chat(self, messages: list[dict],
             tools: list[dict] | None = None,
             personality: str | None = None) -> dict:
        """Non-streaming chat via openZEN with persistent session.

        The first call creates an opencode session and sends the
        system prompt + tool definitions + first user message as
        *one* message.  Subsequent calls send only the *delta*
        (tool results + next user turn) to the same session,
        preserving conversation context server-side.

        Args:
            messages: Conversation history (system, user, assistant, tool).
            tools: Tool definitions injected into the prompt.
            personality: Personality name (``"alvaro"``, ``"elvira"``, etc.).

        Returns:
            Response dict with ``role``, ``content``, optionally
            ``tool_calls`` parsed from the text response, and
            ``usage`` (dict with input/output/reasoning/cache tokens
            and cost as reported by the opencode server).
        """
        num_keys = len(self.api_keys) or 1
        last_error: Exception | None = None

        for attempt in range(num_keys):
            self._last_usage = None
            try:
                reply = self._call_opencode(messages, personality, tools)
            except QuotaError as e:
                logger.warning(
                    "Key #%d exhausted for %s, rotating... (%s)",
                    self.key_index + 1, self.provider_id, e,
                )
                self._rotate_key()
                last_error = e
                continue
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"OpenCode provider error: {e}") from e

            # Parse tool calls from the response text
            tool_calls = self._parse_tool_calls(reply)
            if tool_calls:
                clean_content = re.sub(
                    r'\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}\s*\}',
                    "", reply, flags=re.DOTALL,
                ).strip()
                return {
                    "role": "assistant",
                    "content": clean_content,
                    "tool_calls": tool_calls,
                    "usage": self._last_usage,
                }

            return {
                "role": "assistant",
                "content": reply,
                "usage": self._last_usage,
            }

        # All keys exhausted
        raise RuntimeError(
            f"All {num_keys} API key(s) exhausted for '{self.provider_id}'. "
            f"Last error: {last_error}"
        )

    # ── Stream ────────────────────────────────────────────────────

    def chat_stream(self, messages: list[dict],
                    tools: list[dict] | None = None,
                    personality: str | None = None) -> Generator[str, None, dict]:
        """Streaming chat via openZEN with persistent session.

        For compatibility with the synchronous Flask chat loop we
        collect the full response and yield it as a single token
        (same approach as ``NotebookLMProvider``).
        """
        result = self.chat(messages, tools, personality)
        content = result.get("content", "")
        if content:
            yield content
        return result

    def stream_chat(self, messages: list[dict],
                    tools: list[dict] | None = None,
                    personality: str | None = None) -> Generator[str, None, dict]:
        """REAL streaming chat via openZEN (incremental tokens).

        Opens an SSE subscription to ``GET /event`` for this session,
        sends the prompt with ``POST /session/{id}/message`` in a
        background thread, and yields text *deltas* as they arrive
        from ``message.part.updated`` events.

        Also **auto-replies to permission requests** (``permission.updated``
        events) with ``{"response": "once"}``.  Without this the model can
        stall indefinitely waiting for an interactive user that does not
        exist (the previous 900s "asking" deadlock), because the plain HTTP
        API has no interactive prompt.

        Args:
            messages: Conversation history (system, user, assistant, tool).
            tools: Tool definitions injected into the prompt.
            personality: Personality name (embedded in the messages).

        Yields:
            str: Incremental text deltas as the model generates.

        Returns:
            The final response message dict (same format as ``chat()``),
            including ``usage`` captured from the server-reported tokens.

        Raises:
            QuotaError / RuntimeError: Same semantics as ``_call_opencode``.
        """
        try:
            # ── Build the prompt (first vs delta) like _call_opencode ──
            if not self._first_sent:
                prompt = self._build_first_prompt(messages, personality, tools)
                self._first_sent = True
            else:
                prompt = self._build_delta_prompt(messages, tools)

            session_id = self._get_or_create_session()
            http = self._get_http()

            body: dict = {
                "parts": [{"type": "text", "text": prompt}],
                "modelID": self.model,
                "providerID": self.provider_id,
            }

            # ── Shared state between the POST thread and the SSE reader ──
            stop = threading.Event()
            reply_box: dict = {}          # {"reply": str} or {"error": exc}
            sse_holder: dict = {"resp": None}   # current SSE httpx response

            def _post_message() -> None:
                """Send the message and stash the full reply/error."""
                try:
                    resp = http.post(
                        f"/session/{session_id}/message",
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    # Keep full payload for post-stream error checks
                    # (same surface-errors logic as _send_message).
                    reply_box["data"] = data

                    # Capture token usage + cost (same as _send_message)
                    info = data.get("info") or {}
                    tok = info.get("tokens") or {}
                    cache = tok.get("cache") or {}
                    self._last_usage = {
                        "model_id": info.get("modelID") or self.model,
                        "provider_id": info.get("providerID") or self.provider_id,
                        "input_tokens": tok.get("input", 0),
                        "output_tokens": tok.get("output", 0),
                        "reasoning_tokens": tok.get("reasoning", 0),
                        "cache_read_tokens": cache.get("read", 0),
                        "cache_write_tokens": cache.get("write", 0),
                        "total_tokens": tok.get("total", 0),
                        "cost": info.get("cost", 0),
                    }

                    full_text = ""
                    for part in data.get("parts", []):
                        if part.get("type") == "text":
                            full_text += part.get("text", "")

                    reply_box["reply"] = full_text
                except Exception as e:
                    reply_box["error"] = e
                finally:
                    stop.set()
                    # Unblock the SSE reader by closing its connection.
                    sse = sse_holder.get("resp")
                    if sse is not None:
                        try:
                            sse.close()
                        except Exception:
                            pass

            post_thread = threading.Thread(target=_post_message, daemon=True)
            post_thread.start()

            # ── SSE reader (main thread → can yield) ──────────────────
            last_text = ""                       # total chars emitted via SSE
            part_texts: dict[str, str] = {}      # part_id -> last known text
            try:
                with http.stream(
                    "GET", "/event",
                    # Bounded SSE read (Phase 59, issue #228): a dead
                    # stream (no events for _SSE_READ_TIMEOUT_SECONDS) is
                    # cut so callers get a recoverable error instead of a
                    # task stuck in 'processing' forever. The POST reply
                    # channel keeps the shared client's 900s timeout, so a
                    # long generation that outlives the SSE still lands.
                    timeout=httpx.Timeout(900.0, connect=10.0, read=_SSE_READ_TIMEOUT_SECONDS),
                ) as sse_resp:
                    sse_holder["resp"] = sse_resp
                    event_name: str | None = None
                    for raw_line in sse_resp.iter_lines():
                        if stop.is_set():
                            break
                        if raw_line is None:
                            continue
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:"):].strip()
                            continue
                        if line.startswith("id:"):
                            continue
                        if line.startswith("retry:"):
                            continue
                        if not line.startswith("data:"):
                            continue

                        data_raw = line[len("data:"):].strip()
                        if not data_raw:
                            continue
                        try:
                            payload = json.loads(data_raw)
                        except json.JSONDecodeError:
                            continue

                        ev_type = payload.get("type") or event_name or ""
                        props = payload.get("properties") or {}

                        # ── Auto-accept permission requests ────────────
                        if ev_type == "permission.updated":
                            perm_id = props.get("id")
                            if perm_id:
                                try:
                                    http.post(
                                        f"/session/{session_id}/permissions/{perm_id}",
                                        json={"response": "once"},
                                    )
                                    logger.info(
                                        "Auto-accepted permission %s (session %s)",
                                        perm_id, session_id,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "Failed to auto-accept permission %s: %s",
                                        perm_id, e,
                                    )
                            continue

                        # ── Incremental text deltas ─────────────────────
                        if ev_type == "message.part.updated":
                            part = props.get("part") or {}
                            if part.get("sessionID") != session_id:
                                continue
                            if part.get("type") != "text":
                                continue
                            part_id = part.get("id")
                            part_text = part.get("text") or ""
                            if not part_id:
                                continue
                            prev = part_texts.get(part_id, "")
                            if len(part_text) > len(prev):
                                part_texts[part_id] = part_text
                                delta = part_text[len(prev):]
                                last_text += delta
                                if delta:
                                    yield delta
                            continue

                        # Fallback: full-message text (aggregated parts).
                        # Only used when NO per-part events arrived, to
                        # avoid double-emitting text already yielded.
                        if ev_type == "message.updated" and not part_texts:
                            info = props.get("info") or {}
                            if info.get("sessionID") != session_id:
                                continue
                            parts = info.get("parts") or []
                            full_text = "".join(
                                p.get("text", "")
                                for p in parts
                                if isinstance(p, dict) and p.get("type") == "text"
                            )
                            if len(full_text) > len(last_text):
                                delta = full_text[len(last_text):]
                                last_text = full_text
                                if delta:
                                    yield delta
                            continue
            except Exception as e:
                # SSE failure is NOT fatal — the POST response is the
                # authoritative source; we only lose the live preview.
                if not stop.is_set():
                    logger.warning("OpenCode SSE stream ended early: %s", e)
            finally:
                stop.set()
                # If the SSE ended early (server closed the stream) the
                # POST may still be running — wait for it. It has its own
                # 900s httpx timeout, so this can never hang forever.
                post_thread.join()

            # ── Resolve errors (mirrors _call_opencode mapping) ────────
            error = reply_box.get("error")
            if error is not None:
                if (isinstance(error, httpx.HTTPStatusError)
                        and error.response.status_code in (401, 429)):
                    raise QuotaError(str(error)) from error
                if isinstance(error, httpx.HTTPStatusError):
                    raise RuntimeError(
                        f"OpenCode HTTP {error.response.status_code}: "
                        f"{error.response.text[:200]}"
                    ) from error
                if isinstance(error, httpx.TimeoutException):
                    raise RuntimeError(
                        f"OpenCode request timed out (900s): {error}"
                    ) from error
                if isinstance(error, httpx.RequestError):
                    raise RuntimeError(
                        f"OpenCode connection error: {error}"
                    ) from error
                raise RuntimeError(f"OpenCode provider error: {error}") from error

            # ── Surface server-side errors (same as _send_message) ────
            # A failed message arrives as HTTP 200 with ``info.error``
            # set and no text parts; without this check the caller sees
            # a misleading empty response instead of the real cause.
            data = reply_box.get("data") or {}
            info = data.get("info") or {}
            srv_error = info.get("error")
            if srv_error:
                err_text = (
                    srv_error if isinstance(srv_error, str)
                    else json.dumps(srv_error)
                )
                if _QUOTA_PATTERNS.search(err_text):
                    raise QuotaError(f"OpenCode server error: {err_text}")
                raise RuntimeError(f"OpenCode server error: {err_text}")

            reply = reply_box.get("reply", "")
            if not reply and not last_text:
                finish = info.get("finish")
                part_types = [
                    p.get("type", "?") for p in (data.get("parts") or [])
                ]
                raise RuntimeError(
                    "OpenCode devolvió respuesta sin contenido textual "
                    f"(finish={finish!r}, parts={part_types[:8]})"
                )

            # ── Emit any tail the SSE missed (authoritative reply) ─────
            if len(reply) > len(last_text) and reply[len(last_text):]:
                yield reply[len(last_text):]

            # Same post-processing as chat()
            tool_calls = self._parse_tool_calls(reply)
            if tool_calls:
                clean_content = re.sub(
                    r'\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}\s*\}',
                    "", reply, flags=re.DOTALL,
                ).strip()
                return {
                    "role": "assistant",
                    "content": clean_content,
                    "tool_calls": tool_calls,
                    "usage": self._last_usage,
                }
            return {
                "role": "assistant",
                "content": reply,
                "usage": self._last_usage,
            }
        except (QuotaError, RuntimeError):
            raise
        except Exception as e:
            raise RuntimeError(f"OpenCode provider error: {e}") from e

    # ── Internal: API key management ──────────────────────────────

    def _get_current_key(self) -> str | None:
        """Return the current API key, or None if the pool is empty."""
        if not self.api_keys:
            return None
        return self.api_keys[self.key_index % len(self.api_keys)]

    def _rotate_key(self) -> None:
        """Advance to the next API key and persist the index."""
        if not self.api_keys:
            return
        self.key_index = (self.key_index + 1) % len(self.api_keys)
        self._persist_key_index()

    def _persist_key_index(self) -> None:
        """Save the current key index to ``ai_config.json``."""
        cfg = _get_ai_config()
        cfg["key_index"] = self.key_index
        _save_ai_config(cfg)
        _invalidate_ai_config_cache()

    # ── Tool formatting ───────────────────────────────────────────

    @staticmethod
    def _format_tools(tools: list[dict] | None) -> str:
        """Format tool definitions into an XML block.

        Each tool is rendered with its name, description, and
        parameters so the model knows exactly what is available
        and how to call them.
        """
        if not tools:
            return ""
        parts = ["<tools>"]
        for t in tools:
            fn = t.get("function", t)
            name = fn.get("name", "?")
            desc = fn.get("description", "")
            parts.append(f'  <tool name="{name}">')
            if desc:
                parts.append(f"    <description>{desc}</description>")
            params = fn.get("parameters", {})
            props = params.get("properties", {})
            required = set(params.get("required", []) or [])
            if props:
                for pname, pinfo in props.items():
                    pdesc = pinfo.get("description", "")
                    ptype = pinfo.get("type", "string")
                    req = "required" if pname in required else "optional"
                    enum_vals = pinfo.get("enum")
                    if enum_vals:
                        pdesc += f"  Options: {', '.join(enum_vals)}"
                    parts.append(
                        f'    <parameter name="{pname}" type="{ptype}" '
                        f'required="{req}">{pdesc}</parameter>'
                    )
            parts.append("  </tool>")
        parts.append("</tools>")

        # Few-shot example so big-pickle knows the exact JSON format
        first_tool = None
        for t in tools:
            fn = t.get("function", t)
            if fn.get("name"):
                first_tool = fn["name"]
                break
        if first_tool:
            parts.append(
                f'Example: {{"tool": "{first_tool}", '
                f'"arguments": {{"param1": "value1"}}}}'
            )

        return "\n".join(parts)

    # ── Internal: HTTP call ───────────────────────────────────────

    def _get_http(self) -> httpx.Client:
        """Lazy-init the httpx client (one per provider instance)."""
        if self._http is None:
            headers = {"Accept": "application/json"}
            if self._basic_auth:
                headers["Authorization"] = self._basic_auth
            self._http = httpx.Client(
                base_url=self.server_url,
                # 900s (15 min) — long OpenZEN rewrites (e.g. 20-min audio
                # scripts) can take well over 5 minutes; 300s was too short
                # and produced "OpenCode request timed out (900s)".
                timeout=httpx.Timeout(900.0, connect=10.0),
                headers=headers,
            )
        return self._http

    def _get_or_create_session(self) -> str:
        """Return the existing session ID, or create a new one."""
        if self._session_id:
            return self._session_id
        http = self._get_http()
        resp = http.post("/session", json={})
        resp.raise_for_status()
        data = resp.json()
        self._session_id = data["id"]
        self._first_sent = False
        logger.debug("Created opencode session: %s", self._session_id)
        return self._session_id

    def _send_message(self, text: str) -> str:
        """Send a text message to the current session and return
        the assistant's text reply.

        Also captures the server-reported token usage and cost into
        ``self._last_usage`` so ``chat()`` can attach it to the result.

        Returns:
            The assistant's response text (aggregated from all text
            parts).  Tool-invocation parts are NOT handled here;
            the model is expected to output inline JSON tool calls
            in its text response.
        """
        session_id = self._get_or_create_session()
        http = self._get_http()

        body: dict = {
            "parts": [{"type": "text", "text": text}],
            "modelID": self.model,
            "providerID": self.provider_id,
        }

        resp = http.post(
            f"/session/{session_id}/message",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        # ── Capture token usage + cost (server-reported) ────────────
        info = data.get("info") or {}
        tok = info.get("tokens") or {}
        cache = tok.get("cache") or {}
        self._last_usage = {
            "model_id": info.get("modelID") or self.model,
            "provider_id": info.get("providerID") or self.provider_id,
            "input_tokens": tok.get("input", 0),
            "output_tokens": tok.get("output", 0),
            "reasoning_tokens": tok.get("reasoning", 0),
            "cache_read_tokens": cache.get("read", 0),
            "cache_write_tokens": cache.get("write", 0),
            "total_tokens": tok.get("total", 0),
            "cost": info.get("cost", 0),
        }

        # ── Surface server-side errors instead of returning "" ──────
        # A failed message arrives as HTTP 200 with ``info.error`` set
        # and no text parts.  Returning "" here made callers report
        # misleading "empty response" errors instead of the real cause.
        error = info.get("error")
        if error:
            err_text = (
                error if isinstance(error, str) else json.dumps(error)
            )
            if _QUOTA_PATTERNS.search(err_text):
                raise QuotaError(f"OpenCode server error: {err_text}")
            raise RuntimeError(f"OpenCode server error: {err_text}")

        # Aggregate all text parts into one string
        full_text = ""
        part_types: list[str] = []
        for part in data.get("parts", []):
            part_types.append(part.get("type", "?"))
            if part.get("type") == "text":
                full_text += part.get("text", "")
            # Tool-invocation parts from the server are ignored here;
            # we rely on the model outputting inline JSON tool calls.

        if not full_text:
            finish = info.get("finish")
            raise RuntimeError(
                "OpenCode devolvió respuesta sin contenido textual "
                f"(finish={finish!r}, parts={part_types[:8]})"
            )

        return full_text

    def _build_first_prompt(self, messages: list[dict],
                            personality: str | None = None,
                            tools: list[dict] | None = None) -> str:
        """Build the FIRST message to a fresh opencode session.

        Since the opencode HTTP API does not support ``systemPrompt``,
        we embed everything — personality, instructions, tool
        definitions, and the first user query — into a single text
        message.  This is the only message that carries the full
        context; subsequent messages only carry deltas.

        Format::

            <system>
            [full personality system prompt]
            </system>

            [tool definitions + tool-calling instruction]

            User: [first user message]

            Assistant:
        """
        parts: list[str] = []

        # 0. ENVIRONMENT OVERRIDE — tell the model it is INSIDE Studyflow Hub,
        #    NOT inside OpenCode.  The big-pickle model sees its own system
        #    prompt (OpenCode AGENTS.md with bash/read/write/etc.) and gets
        #    confused, saying "those tools are not available, I only have bash".
        #    This block overrides that assumption.
        env_override = """=== ENVIRONMENT OVERRIDE — CRITICAL: READ CAREFULLY ===
You are running INSIDE Studyflow Hub, NOT in the OpenCode environment.
The standard OpenCode system tools (bash, read, write, edit, glob, grep, webfetch, etc.) do NOT exist here and you CANNOT use them.
The <tools> section below defines your REAL available tools — ONLY those.

=== MANDATORY RULES (you MUST follow these) ===
1. The <tools> below are AVAILABLE and you CAN call them. They are real functions, not suggestions.
2. When the user asks about their agenda, schedule, sessions, or plans:
   - FIRST call list_agenda_sessions to get the data
   - THEN call speak_text to tell the user the result
3. When the user asks about anything else:
   - Call the appropriate tool (list_courses, list_todos, get_day_habits, etc.)
   - THEN call speak_text to respond
4. DO NOT say "those tools are not available" or "I don't have access" — they ARE available, call them.
5. DO NOT say "I can only use bash/read/write" — those do NOT exist here.
=== END OF RULES ===
"""
        parts.append(env_override)

        # 1. Extract system prompt from messages list
        system_text = ""
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content", "") or ""
                if content:
                    system_text += content + "\n"
        system_text = system_text.strip()
        if system_text:
            parts.append(f"<system>\n{system_text}\n</system>")

        # 2. Tool definitions + explicit tool-calling instruction
        tools_block = self._format_tools(tools)
        if tools_block:
            parts.append(tools_block)

        # Inject a STRONG tool-calling instruction right after the
        # tool definitions so the model understands the flow:
        #   1. Call the BUSINESS tool first (e.g. create_agenda_session)
        #   2. Then call speak_text to announce the result
        if tools:
            # Collect non-speak tool names for the instruction
            biz_tools: list[str] = []
            for t in tools:
                fn = t.get("function", t)
                name = fn.get("name", "")
                if name and name != "speak_text":
                    biz_tools.append(name)
            if biz_tools:
                short_list = ", ".join(biz_tools[:6])
                if len(biz_tools) > 6:
                    short_list += ", ..."
                tool_instr = (
                    f"\n=== TOOL CALLING RULE ===\n"
                    f"You MUST call ONE of these tools FIRST: {short_list}\n"
                    f"After you get the result, THEN call speak_text to tell the Señor.\n"
                    f"Example: {{\"tool\": \"{biz_tools[0]}\", "
                    f"\"arguments\": {{\"param1\": \"value1\"}}}}\n"
                )
                parts.append(tool_instr)

        # 3. Conversation history
        non_system = [m for m in messages if m.get("role") != "system"]
        conversation_lines: list[str] = []
        for msg in non_system:
            role = msg.get("role", "user")
            content = msg.get("content", "").strip()
            if not content:
                continue
            if role == "tool" and len(content) > 500:
                content = content[:500] + "…"
            conversation_lines.append(f"{role.capitalize()}: {content}")
        if conversation_lines:
            parts.append("\n".join(conversation_lines))

        # 4. Signal the model to respond
        parts.append("Assistant:")

        return "\n\n".join(parts)

    def _build_delta_prompt(self, messages: list[dict],
                            tools: list[dict] | None = None) -> str:
        """Build a *delta* prompt for the second and subsequent calls.

        At this point the opencode server already has the full
        conversation context from the first message.  We only need
        to send the NEW messages since the last assistant response
        — typically the tool result + the next user turn.

        Includes a short tool-calling reminder so the model does not
        "forget" about tools between turns.

        Format::

            [Tool reminder]
            User: [next query]
            Assistant:
        """
        # Tool reminder — short, just to jog the model's memory
        tool_names: list[str] = []
        if tools:
            for t in tools:
                fn = t.get("function", t)
                name = fn.get("name", "")
                if name and name != "speak_text":
                    tool_names.append(name)
        reminder = ""
        if tool_names:
            names_str = ", ".join(tool_names[:6])
            if len(tool_names) > 6:
                names_str += f" and {len(tool_names) - 6} more"
            reminder = (
                f"=== TOOL REMINDER ===\n"
                f"You MUST output JSON when the request needs a tool.\n"
                f"Available tools include: {names_str}\n"
                f"Example: {{\"tool\": \"{tool_names[0]}\", "
                f"\"arguments\": {{...}}}}"
            )

        # Find non-system messages since the last assistant turn
        non_system = [m for m in messages if m.get("role") != "system"]
        if not non_system:
            return f"{reminder}\n\nAssistant:" if reminder else "Assistant:"

        lines: list[str] = []
        if reminder:
            lines.append(reminder)
        for msg in non_system:
            role = msg.get("role", "user")
            content = msg.get("content", "").strip()
            if not content:
                continue
            if role == "tool" and len(content) > 500:
                content = content[:500] + "…"
            lines.append(f"{role.capitalize()}: {content}")

        lines.append("Assistant:")
        return "\n\n".join(lines)

    def _call_opencode(self, messages: list[dict],
                       personality: str | None = None,
                       tools: list[dict] | None = None) -> str:
        """Send messages to the opencode server and return the reply.

        Manages a persistent session across calls:
          - First call  → create session + send ``_build_first_prompt()``
          - Later calls → send ``_build_delta_prompt()`` to the *same* session

        Args:
            messages: Full message list (system + history).
            personality: Personality name (unused — embedded in messages).

        Returns:
            The assistant's response text from the opencode server.
        """
        try:
            if not self._first_sent:
                # ── First message to this session ──────────────
                prompt = self._build_first_prompt(messages, personality, tools)
                self._first_sent = True
            else:
                # ── Subsequent messages — only the delta ───────
                prompt = self._build_delta_prompt(messages, tools)

            logger.debug(
                "Sending to session %s (first=%s, prompt len=%d)",
                self._session_id, self._first_sent, len(prompt),
            )

            reply = self._send_message(prompt)
            return reply

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 429):
                raise QuotaError(str(e)) from e
            raise RuntimeError(
                f"OpenCode HTTP {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"OpenCode request timed out (900s): {e}"
            ) from e
        except httpx.RequestError as e:
            raise RuntimeError(f"OpenCode connection error: {e}") from e

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _xml_escape(text: str) -> str:
        """Escape XML special characters."""
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        return text

    @staticmethod
    def _extract_balanced_objects(text: str) -> list[str]:
        """Return every balanced ``{...}`` substring in *text*, in order.

        Scans with a depth counter and ignores braces that sit inside JSON
        string literals, so a URL or a code snippet containing ``}`` cannot
        truncate the object.

        A non-greedy regex cannot do this. The v2 pattern
        ``\\{\\s*"tool".*?\\}`` stopped at the *first* closing brace, so
        ``{"tool":"t","arguments":{"a":1,"opts":{"b":2}}}`` matched
        ``{"b":2}``, failed to parse as the tool envelope, and the call went
        out with empty arguments — silently, which is worse than an error.
        """
        out: list[str] = []
        depth = 0
        start = -1
        in_str = False
        escaped = False
        for i, ch in enumerate(text):
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start:i + 1])
                    start = -1
        return out

    @staticmethod
    def _parse_tool_calls(text: str) -> list[dict] | None:
        """Parse JSON tool call blocks from model response text.

        Looks for objects shaped like::

            {"tool": "tool_name", "arguments": {"arg1": "val1"}}

        Returns:
            A list of tool call dicts in the standard format
            (``{"function": {"name": "...", "arguments": {...}}}``),
            or None if no tool calls found.
        """
        tool_calls: list[dict] = []
        for blob in OpenCodeProvider._extract_balanced_objects(text):
            try:
                obj = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            name = obj.get("tool")
            if not isinstance(name, str) or not name:
                continue
            args = obj.get("arguments")
            tool_calls.append({
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, dict) else {},
                }
            })

        return tool_calls or None

    # ── Cleanup ───────────────────────────────────────────────────

    def close(self) -> None:
        """Clean up the HTTP session (if any)."""
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
            self._http = None
        self._session_id = None
        self._first_sent = False
        self._last_usage = None
