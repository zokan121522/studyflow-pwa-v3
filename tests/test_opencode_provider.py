# tests/test_opencode_provider.py
r"""Unit tests for the OpenZen / OpenCode provider.

Three things are pinned here, all of them regressions found while porting the
provider from v2 to v3:

1. The two provider ids must stay distinct. v2 used a single `provider_id`
   attribute both as the name the studyflow config stores API keys under
   ("opencode-acp") *and* as the `providerID` sent to the opencode server
   ("opencode"). The server only accepts its own id, so every request carried
   an unknown provider and the requested model never took effect.
2. `_parse_tool_calls` must survive nested argument objects. The v2 regex
   `\{\s*"tool".*?\}` is non-greedy, so it stopped at the *first* closing
   brace: for
       {"tool":"t","arguments":{"a":1,"opts":{"b":2}}}
   it captured `{"b":2}`, failed to parse, and the tool was invoked with
   empty arguments — silently. A YouTubeZen call carrying nested options
   would have been sent to the model with everything stripped out.

3. Braces and quotes inside string values must not break the scan.

No test here touches the network; the sidecar is exercised separately.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ai.providers import get_provider, list_providers  # noqa: E402
from ai.providers.opencode_provider import (  # noqa: E402
    QuotaError,
    OpenCodeProvider,
)

Parse = OpenCodeProvider._parse_tool_calls


def _args(text):
    """Return the first parsed tool call's arguments, or None if unparsed."""
    calls = Parse(text)
    return calls[0]["function"]["arguments"] if calls else None


# ── Registry ────────────────────────────────────────────────────────


def test_opencode_acp_is_registered():
    names = [p["name"] for p in list_providers()]
    assert "opencode-acp" in names, (
        f"opencode-acp missing from {names}; the OpenZen panel is dead without it"
    )


def test_registry_returns_the_opencode_provider():
    assert isinstance(get_provider("opencode-acp"), OpenCodeProvider)


def test_unknown_provider_lists_the_available_ones():
    with pytest.raises(ValueError) as exc:
        get_provider("definitely-not-a-provider")
    assert "opencode-acp" in str(exc.value)


# ── Provider ids ────────────────────────────────────────────────────


def test_registry_name_and_wire_provider_id_are_separate():
    p = get_provider("opencode-acp")
    # The studyflow-facing name (and ai_config key) keeps the exact v2 spelling.
    assert p.name == "opencode-acp"
    assert p.config_key == "opencode-acp"
    # The wire id is what the opencode server knows itself as.
    assert p.provider_id == "opencode", (
        "providerID must be 'opencode'; anything else is ignored by the server"
    )


def test_api_keys_are_looked_up_by_config_key_not_provider_id():
    """Regression: v2 keyed the lookup by provider_id, so moving the wire id
    would have orphaned every stored key."""
    p = OpenCodeProvider(provider_id="opencode", config_key="opencode-acp")
    assert p.config_key == "opencode-acp"


def test_description_does_not_claim_an_active_key_when_there_are_none():
    """v2 printed 'active #1' with zero keys, which reads like a broken
    key rotation rather than the intended local-model case."""
    p = OpenCodeProvider(api_keys=[])
    assert p.api_keys == []
    assert "active #" not in p.description
    assert "no API key" in p.description


# ── Tool call parsing ───────────────────────────────────────────────


def test_flat_arguments_parse():
    assert _args('{"tool":"t","arguments":{"url":"https://x.com"}}') == {
        "url": "https://x.com"
    }


def test_array_arguments_parse():
    assert _args('{"tool":"t","arguments":{"urls":["a","b"]}}') == {"urls": ["a", "b"]}


def test_nested_arguments_survive():
    """The v2 regex dropped everything before the first inner brace."""
    assert _args(
        '{"tool":"t","arguments":{"url":"https://x.com","opts":{"lang":"es","n":3}}}'
    ) == {"url": "https://x.com", "opts": {"lang": "es", "n": 3}}


def test_deeply_nested_arguments_survive():
    assert _args('{"tool":"t","arguments":{"a":{"b":{"c":{"d":1}}}}}') == {
        "a": {"b": {"c": {"d": 1}}}
    }


def test_brace_inside_a_string_value_does_not_truncate():
    assert _args(
        '{"tool":"t","arguments":{"snippet":"if (x) { y } else { z }"}}'
    ) == {"snippet": "if (x) { y } else { z }"}


def test_escaped_quotes_inside_arguments_survive():
    assert _args('{"tool":"t","arguments":{"s":"say \\"hi\\"","n":1}}') == {
        "s": 'say "hi"',
        "n": 1,
    }


def test_tool_call_embedded_in_prose_is_found():
    text = 'Aqui tienes:\n{"tool":"search","arguments":{"q":"a}b","deep":{"x":1}}}\nlisto'
    assert _args(text) == {"q": "a}b", "deep": {"x": 1}}


def test_multiple_tool_calls_are_all_returned():
    calls = Parse(
        '{"tool":"a","arguments":{"x":1}} y luego {"tool":"b","arguments":{"y":2}}'
    )
    assert [c["function"]["name"] for c in calls] == ["a", "b"]


def test_plain_prose_yields_no_tool_calls():
    assert Parse("solo texto normal, sin herramientas") is None


def test_unrelated_json_is_not_mistaken_for_a_tool_call():
    assert Parse('{"foo":1,"bar":2}') is None


def test_malformed_tool_envelope_does_not_raise():
    # Truncated output (the model hit a token cap) must not blow up the caller.
    assert Parse('{"tool":"t","arguments":{"a":') is None


def test_arguments_of_the_wrong_type_degrade_to_empty_dict():
    assert _args('{"tool":"t","arguments":"nope"}') == {}


# ── Quota detection ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "429 Too Many Requests",
        "401 unauthorized",
        "insufficient_quota",
        "rate limit exceeded",
        "credit limit reached",
    ],
)
def test_quota_messages_are_recognised(message):
    from ai.providers.opencode_provider import _QUOTA_PATTERNS

    assert _QUOTA_PATTERNS.search(message), f"not detected: {message}"


def test_quota_error_is_a_runtime_error():
    assert issubclass(QuotaError, RuntimeError)
