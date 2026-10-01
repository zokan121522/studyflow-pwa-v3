"""get_active_profile() must never hand back a profile with no session.

The failure on 2026-10-01, twice in a row:

    ❌ Storage file not found: .../profiles/default/storage_state.json

A valid session (15 KB, `profiles/zokan121522/storage_state.json`) was sitting
right there, and `user_config.notebooklm_profile` already named the user. The
generation path only ever read `active.txt`; when that file was gone it fell
through to a global/default directory that does not exist and failed with an
error pointing at storage rather than at the real cause — the user was told to
run `notebooklm login` while his cookies were fine.

These tests pin the resolution rules against a fake profile tree, so the
behaviour is verifiable without touching the real profiles.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ai.notebooklm import utils  # noqa: E402


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A fake profiles/ tree; returns a helper to write a profile."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(utils, "PROFILES_DIR", str(profiles))
    monkeypatch.setattr(utils, "ACTIVE_FILE", str(tmp_path / "active.txt"))

    def write(name: str, payload: str = '{"cookies":[]}') -> str:
        d = profiles / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "storage_state.json").write_text(payload)
        return name

    write.path = profiles  # type: ignore[attr-defined]
    return write


def _set_active(tree, email: str | None) -> None:
    utils.ACTIVE_FILE = str(Path(tree.path).parent / "active.txt")
    if email:
        Path(utils.ACTIVE_FILE).write_text(email)


def test_active_file_is_used_when_it_has_a_session(tree):
    tree("ana@example.com")
    _set_active(tree, "ana@example.com")
    assert utils.get_active_profile() == "ana@example.com"


def test_active_txt_missing_recovers_the_only_connected_profile(tree):
    """The exact case from the bug report."""
    tree("zokan121522")
    _set_active(tree, None)  # active.txt absent
    assert utils.get_active_profile() == "zokan121522"


def test_active_txt_empty_also_recovers(tree):
    tree("zokan121522")
    _set_active(tree, "")
    assert utils.get_active_profile() == "zokan121522"


def test_empty_storage_state_is_not_a_session(tree):
    """An interrupted login leaves a 0-byte file. Never select it."""
    tree("ana@example.com", payload="")  # 0 bytes
    _set_active(tree, "ana@example.com")
    assert utils.get_active_profile() is None


def test_missing_storage_file_is_not_a_session(tree):
    """Profile dir exists but has no storage_state.json at all."""
    (tree.path / "broken").mkdir()
    _set_active(tree, "broken")
    assert utils.get_active_profile() is None


def test_active_profile_falls_back_when_its_session_is_empty(tree):
    """active.txt names the broken one; a healthy one exists → use it."""
    tree("broken", payload="")
    tree("healthy")
    _set_active(tree, "broken")
    assert utils.get_active_profile() == "healthy"


def test_full_email_resolves_to_local_part_directory(tree):
    """active.txt holds `x@gmail.com`, the upload path created `x`."""
    tree("zokan121522")
    _set_active(tree, "zokan121522@gmail.com")
    assert utils.get_active_profile() == "zokan121522"


def test_empty_homonym_does_not_shadow_the_connected_one(tree):
    """Both dirs exist and the exact-name one is the empty leftover."""
    tree("zokan121522@gmail.com", payload="")   # interrupted login
    tree("zokan121522")                         # the real session
    _set_active(tree, "zokan121522@gmail.com")
    assert utils.get_active_profile() == "zokan121522"


def test_ambiguous_returns_none_and_logs_why(tree, caplog):
    """Two healthy profiles and no active choice: refuse, and say so."""
    tree("a@x.com")
    tree("b@x.com")
    _set_active(tree, None)
    with caplog.at_level("WARNING", logger="notebooklm.utils"):
        assert utils.get_active_profile() is None
    assert "connected profiles" in caplog.text, (
        "un diagnóstico:'no sé cuál usar' con la lista, no un callejón sin salida"
    )


def test_nothing_connected_returns_none(tree, caplog):
    _set_active(tree, None)
    with caplog.at_level("WARNING", logger="notebooklm.utils"):
        assert utils.get_active_profile() is None
    assert "re-authenticate" in caplog.text, (
        "debe decir que hay que volver a autenticarse"
    )


def test_whitespace_only_active_file_is_treated_as_empty(tree):
    tree("zokan121522")
    _set_active(tree, "   \n ")
    assert utils.get_active_profile() == "zokan121522"


def test_profiles_dir_missing_does_not_raise(tmp_path, monkeypatch):
    """A wiped volume must not crash generation with a traceback."""
    monkeypatch.setattr(utils, "PROFILES_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(utils, "ACTIVE_FILE", str(tmp_path / "nope.txt"))
    assert utils.get_active_profile() is None


def test_connected_profiles_lists_only_usable_ones(tree):
    tree("good1")
    tree("good2")
    tree("empty", payload="")
    assert utils._connected_profiles() == ["good1", "good2"]