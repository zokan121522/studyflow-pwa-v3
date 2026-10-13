"""Unit tests for the TTS engine's macOS `say` fallback.

These never touch the network or real audio devices: edge-tts is replaced
by a stub, and the `say`/`afconvert` subprocess calls are mocked.
"""

from __future__ import annotations

import pytest

import tts_engine
from tts_engine import TTSError, TtsResult


def _raise_edge(*_args, **_kwargs):
    raise TTSError("edge-tts 403 WSServerHandshakeError")


def test_platform_guard_only_on_macos_with_tools():
    which_all = lambda name: f"/usr/bin/{name}"  # noqa: E731
    assert tts_engine.platform_supports_say_fallback("darwin", which_all)
    assert not tts_engine.platform_supports_say_fallback("win32", which_all)
    assert not tts_engine.platform_supports_say_fallback("linux", which_all)
    # macOS but the tools are missing -> no fallback
    assert not tts_engine.platform_supports_say_fallback("darwin", lambda _n: None)


def test_fallback_chosen_on_darwin(monkeypatch):
    monkeypatch.setattr(tts_engine, "_edge_tts_synthesize", _raise_edge)
    monkeypatch.setattr(tts_engine, "platform_supports_say_fallback", lambda *a, **k: True)

    seen = {}

    def fake_say(text, edge_voice, output_path, rate="+0%", timeout=60.0):
        seen["voice"] = edge_voice
        seen["out"] = output_path
        return TtsResult(path="/tmp/x.m4a", engine="say", mimetype="audio/mp4")

    monkeypatch.setattr(tts_engine, "_say_synthesize", fake_say)

    result = tts_engine.synthesize("hola", "es-ES-AlvaroNeural", "/tmp/x.mp3")
    assert result.engine == "say"
    assert result.mimetype == "audio/mp4"
    assert seen["voice"] == "es-ES-AlvaroNeural"


def test_no_fallback_on_non_macos(monkeypatch):
    monkeypatch.setattr(tts_engine, "_edge_tts_synthesize", _raise_edge)
    monkeypatch.setattr(tts_engine, "platform_supports_say_fallback", lambda *a, **k: False)

    called = []
    monkeypatch.setattr(tts_engine, "_say_synthesize", lambda *a, **k: called.append(1))

    with pytest.raises(TTSError):
        tts_engine.synthesize("hola", "es-ES-AlvaroNeural", "/tmp/x.mp3")
    assert not called


def test_no_fallback_when_disabled(monkeypatch):
    monkeypatch.setattr(tts_engine, "_edge_tts_synthesize", _raise_edge)
    monkeypatch.setattr(tts_engine, "platform_supports_say_fallback", lambda *a, **k: True)

    called = []
    monkeypatch.setattr(tts_engine, "_say_synthesize", lambda *a, **k: called.append(1))

    with pytest.raises(TTSError):
        tts_engine.synthesize(
            "hola", "es-ES-AlvaroNeural", "/tmp/x.mp3", allow_fallback=False
        )
    assert not called


def test_edge_success_skips_fallback(monkeypatch, tmp_path):
    def fake_edge(text, voice, output_path, rate="+0%", volume="+0%", timeout=30.0):
        (tmp_path / "o.mp3").write_bytes(b"audio")

    monkeypatch.setattr(tts_engine, "_edge_tts_synthesize", fake_edge)
    called = []
    monkeypatch.setattr(tts_engine, "_say_synthesize", lambda *a, **k: called.append(1))

    result = tts_engine.synthesize("hi", "es-ES-AlvaroNeural", str(tmp_path / "o.mp3"))
    assert result.engine == "edge-tts"
    assert result.mimetype == "audio/mpeg"
    assert result.path.endswith(".mp3")
    assert not called


def test_say_voice_mapping():
    assert tts_engine.say_voice_for("es-ES-AlvaroNeural") == "Mónica"
    assert tts_engine.say_voice_for("es-ES-ElviraNeural") == "Mónica"
    assert tts_engine.say_voice_for("en-US-ChristopherNeural") == "Samantha"
    assert tts_engine.say_voice_for("fr-FR-DeniseNeural") == "Mónica"


def test_rate_to_wpm():
    assert tts_engine._rate_to_wpm("+0%") is None
    assert tts_engine._rate_to_wpm("") is None
    assert tts_engine._rate_to_wpm("+100%") == 350
    assert tts_engine._rate_to_wpm("-50%") == 90  # clamped low


def test_say_synthesize_builds_commands(monkeypatch, tmp_path):
    runs = []

    class _P:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kw):
        runs.append(cmd)
        out = cmd[cmd.index("-o") + 1] if cmd[0] == "say" else cmd[-1]
        with open(out, "wb") as fh:
            fh.write(b"data")
        return _P()

    monkeypatch.setattr(tts_engine.subprocess, "run", fake_run)

    result = tts_engine._say_synthesize(
        "hola que tal", "es-ES-AlvaroNeural", str(tmp_path / "o.mp3")
    )
    assert result.engine == "say"
    assert result.mimetype == "audio/mp4"
    assert result.path.endswith(".m4a")
    assert runs[0][0] == "say"
    assert runs[0][1] == "-v"
    assert runs[0][2] == "Mónica"
    assert runs[1][0] == "afconvert"
    assert runs[1][1:3] == ["-f", "mp4f"]
