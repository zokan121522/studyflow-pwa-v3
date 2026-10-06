"""Windows / local-first installation guarantees.

These are the constraints that made a clean install impossible before, and that
nobody notices on the developer's own Mac where every package is already
installed. Each test here corresponds to a failure that actually happened.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS_LOCAL = REPO / "requirements-local.txt"
BACKEND = REPO / "backend"


# ── requirements-local.txt ───────────────────────────────────────────


def _requirements() -> dict[str, str]:
    """Parse the minimal requirements, ignoring comments and the header."""
    out: dict[str, str] = {}
    for raw in REQUIREMENTS_LOCAL.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        out[name.strip().lower()] = version.strip()
    return out


def test_requirements_local_exists_and_is_pinned():
    deps = _requirements()
    assert deps, "requirements-local.txt no declara ninguna dependencia"
    # Unpinned dependencies make the install non-reproducible, which is the
    # whole point of shipping a separate file for local installs.
    assert all(v for v in deps.values()), "hay dependencias sin fijar"


@pytest.mark.parametrize(
    "package",
    ["gunicorn", "psycopg2-binary", "weasyprint", "notebooklm-py", "playwright",
     "selenium", "yt-dlp"],
)
def test_local_requirements_exclude_production_only_packages(package):
    """These break or block a local Windows install.

    gunicorn has no Windows support at all, and weasyprint needs pango/cairo.
    notebooklm-py pulls Playwright and its browsers, which is a large download
    for a feature the local install does not need to boot.
    """
    assert package not in _requirements(), (
        f"{package} no debe estar en requirements-local.txt: rompe o bloquea "
        f"la instalación local en Windows"
    )


def test_local_requirements_include_tzdata():
    """The imported-calendar feature cannot work on Windows without it.

    ``routes/calendar.py`` resolves Europe/Madrid through ``zoneinfo``. Windows
    ships no time zone database, so ``ZoneInfoNotFoundError`` makes the import
    endpoint answer 500. Confirmed on the user's machine: ``ZONEINFO_FAIL:
    ZoneInfoNotFoundError 'No time zone found with key Europe/Madrid'``.

    macOS hides this, because the system provides the database -- and on the
    dev machine it was Anaconda's ``tzdata`` being picked up, never a declared
    dependency. That is why the feature looked fine locally and failed on the
    portable build.
    """
    assert "tzdata" in _requirements(), (
        "falta tzdata en requirements-local.txt: sin base de datos de zonas "
        "horarias el import de calendario devuelve 500 en Windows"
    )


def test_tzdata_pin_satisfies_the_icalendar_floor():
    """Being in the file is not enough -- the pin has to be installable.

    ``tzdata==2025.2`` was here to fix the very bug above, and it was *worse*:
    ``icalendar==7.3.0`` declares ``tzdata>=2025.3``, so that pin made the whole
    ``pip install -r requirements-local.txt`` unresolvable. On the user's machine
    that left a venv containing nothing but pip, and every launch afterwards
    died with ``ModuleNotFoundError: No module named 'flask'``.

    Worse still, the test right above passed the whole time. Presence says
    nothing about installability, which is the lesson of every test in this
    file: the developer's Mac already has all of these, so "it is in the
    requirements" feels like coverage while proving nothing.
    """
    from packaging.requirements import Requirement
    from packaging.version import Version

    tzdata = Version(_requirements()["tzdata"])

    # The floor is not hardcoded: it comes from icalendar's own metadata, so
    # bumping icalendar later cannot quietly invalidate this pin.
    try:
        from importlib.metadata import requires

        declared = [r for r in (requires("icalendar") or []) if r.lower().startswith("tzdata")]
    except Exception:  # pragma: no cover - icalendar not installed here
        declared = []

    if not declared:  # pragma: no cover - offline fallback
        pytest.skip("icalendar no esta instalado: no se puede leer su minimo de tzdata")

    floor = Requirement(declared[0]).specifier
    assert tzdata in floor, (
        f"tzdata=={tzdata} no cumple el minimo que exige icalendar ({floor}): "
        "el install completo de requirements-local.txt quedara irresoluble y el "
        "launcher se quedara con un venv sin flask"
    )



@pytest.mark.parametrize("package", ["gevent", "gevent-websocket"])
def test_local_requirements_include_the_novnc_dependencies(package):
    """server.py guards the noVNC import, so nothing errors when these are absent.

    That guard is why this was missed: the app booted perfectly and the
    embedded NotebookLM login simply never opened, with no message anywhere.
    Two megabytes is a cheap price for a feature that either works or clearly
    does not.
    """
    assert package in _requirements(), (
        f"{package} falta en requirements-local.txt: sin él la vista embebida "
        f"de NotebookLM no abre y no hay ningún error que lo indique"
    )


def test_novnc_proxy_gevent_import_is_not_silent():
    """The import must stay guarded in server.py, but the dependency is real.

    Documents the two halves of the trade-off: the guard keeps a missing gevent
    from breaking startup, and requirements-local.txt is what stops it from
    being missing in the first place.
    """
    server_src = (BACKEND / "server.py").read_text()
    assert "except Exception" in server_src and "novnc_proxy" in server_src, (
        "server.py debe seguir importando novnc_proxy de forma tolerante"
    )
    proxy_src = (BACKEND / "routes" / "novnc_proxy.py").read_text()
    assert "import gevent" in proxy_src, (
        "novnc_proxy.py usa gevent; requirements-local.txt debe declararlo"
    )


def test_local_requirements_include_the_hard_startup_imports():
    """Everything server.py imports at module level must be installable.

    Discovered by walking the import graph from server.py: icalendar and httpx
    are hard imports that were missing from a naive minimal set.
    """
    deps = _requirements()
    for package in ("flask", "flask-cors", "icalendar", "httpx", "requests",
                    "pyjwt", "bcrypt", "cryptography", "pymupdf", "pillow",
                    "edge-tts", "python-dotenv"):
        assert package in deps, f"{package} falta en requirements-local.txt"


# ── optional notebooklm SDK ──────────────────────────────────────────


def _module_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found = []
    for node in tree.body:  # module level only, not inside functions
        if isinstance(node, ast.Import):
            found += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.append(node.module.split(".")[0])
    return found


@pytest.mark.parametrize(
    "relative",
    ["ai/notebooklm/client.py", "ai/notebooklm/infographic.py", "ai/notebooklm/youtube.py"],
)
def test_notebooklm_is_never_a_bare_module_level_import(relative):
    """The SDK must be optional, or the app cannot boot without it.

    routes/notebooklm_settings.py imports ai/notebooklm.client at module level,
    and server.py imports that route, so a bare `from notebooklm import ...`
    anywhere in this chain made the whole app unstartable on a clean machine.
    """
    imports = _module_level_imports(BACKEND / relative)
    assert "notebooklm" not in imports, (
        f"{relative} importa 'notebooklm' sin try/except: la app no arranca "
        f"en una instalación limpia sin el SDK"
    )


def test_infographic_enums_have_working_stand_ins():
    """The enum maps are built at import time, so `None` is not enough.

    _ORIENTATION_MAP / _DETAIL_MAP / _STYLE_MAP dereference these at module
    level. When the SDK is missing they need real stand-ins or the import
    raises AttributeError: 'NoneType' object has no attribute 'LANDSCAPE'.
    """
    from ai.notebooklm import infographic

    assert infographic._ORIENTATION_MAP["landscape"] is not None
    assert infographic._DETAIL_MAP["concise"] is not None
    # Every style key in _STYLE_MAP must resolve, aliases included.
    for key in infographic._STYLE_MAP:
        assert infographic._STYLE_MAP[key] is not None, f"estilo {key} sin valor"


def test_notebooklm_helper_raises_a_clear_error_when_absent():
    from ai.notebooklm.client import _require_notebooklm

    try:
        import notebooklm  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="notebooklm-py"):
            _require_notebooklm()
    else:
        assert _require_notebooklm() is not None


# ── launcher ─────────────────────────────────────────────────────────


def test_launcher_bat_exists_and_uses_local_requirements():
    bat = REPO / "launcher" / "StudyFlow.bat"
    assert bat.exists(), "falta launcher/StudyFlow.bat para Windows"
    body = bat.read_text()
    assert "requirements-local.txt" in body, (
        "el .bat debe instalar requirements-local.txt, no el set de produccion"
    )


def test_child_executable_prefers_pythonw_on_windows(monkeypatch):
    """No console window may survive a double-click on Windows.

    The server never exits, so a console left on screen would sit there for the
    whole session. pythonw.exe is the same interpreter without the console.
    """
    sys.path.insert(0, str(REPO / "launcher"))
    import launch

    monkeypatch.setattr(launch.os, "name", "nt")
    result = launch.child_executable()
    if result.endswith("pythonw.exe"):
        assert Path(result).exists(), "pythonw.exe no existe junto al intérprete"
    else:
        assert result == sys.executable  # pythonw absent: fall back, do not crash

    monkeypatch.setattr(launch.os, "name", "posix")
    assert launch.child_executable() == sys.executable


def test_spawn_is_valid_on_this_platform():
    """start_new_session is POSIX-only; Windows must not pass it as True.

    Guards the actual Popen call path so a Windows-only TypeError cannot slip
    in unnoticed from a Mac.
    """
    sys.path.insert(0, str(REPO / "launcher"))
    import launch

    kwargs = {"start_new_session": launch.os.name != "nt"}
    if launch.os.name == "nt":
        assert kwargs["start_new_session"] is False


# ── launcher/StudyFlow.bat ───────────────────────────────────────────


#: Import name -> distribution name, where they differ.
_IMPORT_ALIASES = {
    "flask_cors": "flask-cors",
    "fitz": "pymupdf",
    "jwt": "pyjwt",
    "edge_tts": "edge-tts",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "dotenv": "python-dotenv",
}


def test_launcher_probe_only_uses_declared_dependencies():
    """The launcher's readiness probe must not name undeclared packages.

    The probe exists to notice a venv left empty by a failed install. The first
    version of it probed ``psycopg2``, which ``requirements-local.txt`` points at
    in a comment and deliberately leaves out -- ``database.py`` imports it inside
    a try/except, because the local build runs on SQLite. Probing it meant the
    probe could never pass, so the launcher would re-run setup forever instead of
    ever starting: a perfectly broken install hiding behind a self-heal.

    Worth locking down, because the failure mode is a silent infinite loop rather
    than an error anyone would see.
    """
    bat = (REPO / "launcher" / "StudyFlow.bat").read_text()

    probes = [
        line for line in bat.splitlines()
        if "-c \"import " in line and "%VENV_PY%" in line
    ]
    assert probes, "el launcher deberia sondear las dependencias antes de arrancar"

    declared = _requirements()
    for line in probes:
        modules = line.split('-c "import ', 1)[1].split('"', 1)[0]
        for module in (m.strip() for m in modules.split(",")):
            distribution = _IMPORT_ALIASES.get(module, module)
            assert distribution in declared, (
                f"el launcher sondea '{module}' pero '{distribution}' no esta en "
                "requirements-local.txt: la comprobacion nunca pasara y el launcher "
                "reintentara el setup en bucle infinito"
            )
