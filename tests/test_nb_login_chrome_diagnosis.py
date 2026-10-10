"""The NotebookLM login must not blame the user for a missing server browser.

Failure story this guards (2026-10-01):

    User changes NotebookLM account in the app → "Conectado: zokan121522"
    → clicks generate → ❌ "Authentication expired or invalid … Run
      'notebooklm login' to re-authenticate."
    → clicks "Iniciar sesión" → ❌ "El proceso de login falló al arrancar.
      Verifica que `notebooklm` está disponible y playwright instalado."

Both messages were wrong in a way that cost the user a whole debugging round:

  * The task error blamed a stale Google session. The real cause was the login
    never running, so the profile stayed 0 bytes.
  * The login error blamed notebooklm and playwright — **both of which were
    installed and working**. It actually wanted the *desktop* Chrome, which
    Playwright does not bundle: `--browser chrome` resolves through the
    `channel` parameter (system browsers only), while the bundled Chromium in
    PLAYWRIGHT_BROWSERS_PATH is a different binary.

`notebooklm login` already says the right thing
("Google Chrome not found. Run playwright install chrome"), but this route
swallowed stderr into DEVNULL and replaced it with a guess. So the assertions
here are about *diagnosis*, not about the login succeeding.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402

LOGIN = ROOT / "backend" / "routes" / "notebooklm_login.py"
DOCKERFILE = ROOT / "backend" / "Dockerfile"


def _source() -> str:
    return LOGIN.read_text()


def test_pid_alive_probe_cannot_kill_on_windows():
    """login-status must never kill the login window it is polling.

    os.kill(pid, 0) is a harmless existence probe on POSIX, but on Windows
    CPython documents that *any* signal other than CTRL_C_EVENT /
    CTRL_BREAK_EVENT terminates the process via TerminateProcess with the
    signal as exit code. The first login-status poll therefore murdered our
    Python login helper on the user's machine (Windows): the Google window
    blinked out mid-login, no cookies were ever written, and the app reported
    a failed sign-in. The fix probes with OpenProcess on Windows.
    """
    src = _source()
    region = src.split("def _pid_alive", 1)[1].split("def _cleanup", 1)[0]
    assert 'os.name == "nt"' in region, "debe ramificar por plataforma"
    assert "OpenProcess" in region, (
        "Windows necesita un probe con OpenProcess; os.kill(pid, 0) mata "
        "(TerminateProcess con exit code = sig)"
    )
    win_helper = region.split("def _win_pid_alive", 1)[1].split("def _kill_pid", 1)[0]
    # Saltar el docstring: solo interesa el código ejecutable.
    win_code = win_helper.split('"""', 1)[1].split('"""', 1)[1]
    assert "os.kill" not in win_code, (
        "el probe de Windows nunca puede llamar a os.kill"
    )


def test_login_route_still_drives_desktop_chrome():
    """The login must still launch the *desktop* Chrome (channel=chrome)."""
    src = _source()
    assert "nb_login_browser.py" in src, (
        "el login local debe ir por nuestro helper (CLI 0.8.4 false-positiva "
        "'Already logged in' contra la SPA de notebook.google.com)"
    )
    assert '"--profile-dir", browser_profile' in src, (
        "el helper debe recibir el perfil persistente de Chrome"
    )
    assert '"--storage", storage_path' in src


def test_chrome_is_probed_before_launching():
    """Probe first: it turns a lying 500 into an accurate one."""
    src = _source()
    assert "_missing_desktop_chrome()" in src, "debe comprobar Chrome antes de lanzar"
    # The probe must run BEFORE the Popen, otherwise it cannot help.
    assert src.index("_missing_desktop_chrome()") < src.index("subprocess.Popen"), (
        "la comprobación va después del Popen: llega tarde"
    )


def test_probe_checks_system_browsers_not_the_bundled_one():
    """The whole bug was these being confused. Pin the distinction."""
    src = _source()
    assert "shutil.which" in src, "debe buscar el ejecutable en el sistema"
    assert "google-chrome" in src, "debe conocer el nombre real del binario"
    # A probe that accepted the bundled path would report healthy forever.
    assert "ms-playwright" not in src.split("def _read_state")[0], (
        "la comprobación no debe mirar PLAYWRIGHT_BROWSERS_PATH: ese Chromium "
        "no sirve para --browser chrome"
    )


def test_missing_chrome_reports_the_real_cause():
    """No blaming notebooklm or playwright when both are installed.

    Parses the actual message literal out of the source rather than
    substring-matching a window around it, so reformatting the message does
    not read as a regression.
    """
    src = _source()
    m = re.search(r'"missing_chrome":\s*True,.*?"message":\s*\((.*?)\)\s*,?\s*\}',
                  src, re.S)
    assert m, "no se encontró el bloque missing_chrome"
    message = " ".join(re.findall(r'"([^"]*)"', m.group(1))).lower()

    assert "chrome" in message, "el mensaje debe nombrar a Chrome, la causa real"
    # The old lie, verbatim — this is the string that misled the user.
    assert "playwright instalado" not in message
    assert "no es un problema de tu cuenta" in message, (
        "debe tranquilizar al usuario: la causa es el servidor, no su cuenta"
    )


def test_dockerfile_installs_desktop_chrome():
    """The fix for the environment itself lives in the image."""
    dockerfile = DOCKERFILE.read_text()
    assert re.search(r"playwright install .*chrome", dockerfile), (
        "backend/Dockerfile debe instalar Chrome, o el login seguirá fallando "
        "en cualquier rebuild"
    )
    # Chromium bundled is still required — audio/infographics/flashcards use it.
    assert "playwright install --with-deps chromium" in dockerfile, (
        "no se debe tocar la instalación de Chromium: el resto de funciones "
        "de NotebookLM depende de ella"
    )


def test_stderr_is_not_swallowed_silently():
    """The original sin: stderr→DEVNULL is why nobody saw the real error."""
    src = _source()
    popen = src.split("login_proc = subprocess.Popen")[1][:300]
    assert "stderr=subprocess.DEVNULL" not in popen, (
        "capturar stderr a un pipe/log: es lo que escondía el error real"
    )


# ── Windows-local port (2026-10-06) ──────────────────────────────────────

def _login_module():
    import importlib
    with pytest.MonkeyPatch.context() as mp:
        sys.path.insert(0, str(ROOT / "backend"))
        return importlib.import_module("routes.notebooklm_login")


def test_windows_chrome_found_in_program_files(monkeypatch):
    """Chrome on Windows lives in Program Files, not on PATH.

    The whole login button died on this: shutil.which() answered None, so the
    guard claimed "Falta Google Chrome" while the browser was installed under
    C:\Program Files\Google\Chrome (Playwright's channel='chrome' resolves it
    fine through the registry — only our probe was blind to it).
    """
    login = _login_module()
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("PROGRAMFILES", "C:/PROGRA~1")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/x/AppData/Local")
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(
        "os.path.isfile",
        lambda p: p.endswith("chrome.exe") and "PROGRA~1" in p.replace("\\", "/"),
    )
    assert login._missing_desktop_chrome() is False


def test_windows_chrome_still_reported_missing_when_absent(monkeypatch):
    """The guard must still bite when Chrome really is gone."""
    login = _login_module()
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("PROGRAMFILES", "C:/PROGRA~1")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/x/AppData/Local")
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr("os.path.isfile", lambda p: False)
    assert login._missing_desktop_chrome() is True


def test_state_file_is_not_a_hardcoded_posix_tmp():
    """Windows has no /tmp: the hardcoded path would 500 on every state write.

    ``/tmp/notebooklm_login.json`` resolves to C:\tmp\... on Windows, which
    does not exist, so _write_state() raised FileNotFoundError after the login
    already launched — user saw Chrome open and a 500 at the same time.
    """
    import tempfile
    login = _login_module()
    tmp = tempfile.gettempdir()
    assert tmp in login.STATE_FILE, (
        f"STATE_FILE debe estar en el tempdir del sistema (Windows no tiene "
        f"/tmp): {login.STATE_FILE}"
    )
    assert not login.STATE_FILE.startswith("/tmp/"), (
        "ruta POSIX heredada: la app la escribía fuera de Windows"
    )


# ── NotebookLM CLI false-positive on the SPA (2026-10-06) ───────────────

HELP_SCRIPT = ROOT / "backend" / "scripts" / "nb_login_browser.py"


def test_helper_opens_desktop_chrome_and_waits_for_oauth_cookies():
    """Pin the fix for "el navegador se cerró sin guardar cookies".

    Failure story this guards:

        ``notebooklm login --browser chrome`` reports "Already logged in."
        on a *brand-new* profile because notebook.google.com answers HTTP 200
        with the SPA shell even when unauthenticated — the CLI's URL-based
        landing check never sees the login redirect, captures domain cookies
        only (SID missing), writes junk, and closes Chrome without ever
        showing the sign-in form. Verified empirically twice on 2026-10-06,
        including with a profile created seconds earlier.

    The helper must:
      1. drive the *desktop* Chrome (channel=chrome),
      2. open the server-rendered accounts.google.com form, NOT the SPA,
      3. decide by waiting for the real OAuth cookies (SID + __Secure-1PSIDTS)
         read from the LIVE browser context,
      4. persist storage_state.json only after they exist.
    """
    script = HELP_SCRIPT.read_text()
    assert 'channel="chrome"' in script, (
        "debe usar el Chrome de escritorio, no el Chromium empaquetado"
    )
    assert "__Secure-1PSIDTS" in script, "debe esperar a la cookie OAuth real"
    assert "REQUIRED.issubset" in script, "el criterio son las cookies, no la URL"
    assert "AUTH_OK" in script, "debe marcar el fin de autenticación"
    # The cookies must be read from the live browser BEFORE persisting.
    assert script.index("ctx.cookies()") < script.index("AUTH_OK"), (
        "persiste antes de leer las cookies del navegador vivo: no sirve"
    )
    # Google blocks automated browsers ("This browser or app may not be
    # secure"); the SDK ships these flags precisely to avoid that.
    assert "--disable-blink-features=AutomationControlled" in script, (
        "faltan los flags anti-detección: Google corta el login"
    )
    assert 'ignore_default_args=["--enable-automation"]' in script, (
        "sin ignorar --enable-automation, navigator.webdriver=true"
    )


def test_login_start_clears_stale_storage_but_keeps_the_profile():
    """Storage_state is deleted (fresh detection); the browser_profile is kept
    so the helper can reuse a still-live session instead of forcing re-login."""
    src = _source()
    assert "os.remove(storage_path)" in src, (
        "el storage stale se sigue limpiando (login-status detecta lo nuevo)"
    )
    assert "shutil.rmtree(browser_profile)" not in src, (
        "ya no se borra el perfil: el helper decide por cookies, no por URL "
        "(un perfil vivo ahorra re-loguear)"
    )


def test_login_start_removes_stale_profile_and_launches(monkeypatch, tmp_path):
    """Functional: stale storage is deleted and the chrome path still runs,
    invoking our helper with the persistent profile + storage."""
    import flask
    login = _login_module()

    profile_dir = tmp_path / "profiles" / "zokan@example.com"
    (profile_dir / "browser_profile" / "Default").mkdir(parents=True)
    (profile_dir / "storage_state.json").write_text("{}")
    monkeypatch.setattr(login, "COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(login, "_missing_desktop_chrome", lambda: False)
    monkeypatch.setattr(login, "_cleanup", lambda: None)
    monkeypatch.setattr(login, "set_active_profile", lambda *a, **k: None)
    monkeypatch.setattr(login, "_write_state", lambda *a, **k: None)
    monkeypatch.setattr(login, "_schedule_timeout", lambda: None)

    calls = {}

    class FakeProc:
        pid = 4242

        @staticmethod
        def poll():
            return None  # alive past the 1s check

    def fake_popen(cmd, **kw):
        calls["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(login.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        login,
        "request",
        type("R", (),
             {"get_json": lambda self, silent=True: {"account": "zokan@example.com"},
              "scheme": "http"})(),
    )
    app = flask.Flask(__name__)
    with app.test_request_context():
        result = login.login_start()  # token_required inyecta LOCAL_USER_ID
    if isinstance(result, tuple):
        result = result[0]
    assert result.status_code == 200, result.get_data(as_text=True)
    assert not (profile_dir / "storage_state.json").exists(), (
        "el storage stale debe limpiarse (login-status detecta lo nuevo)"
    )
    assert (profile_dir / "browser_profile").exists(), (
        "el perfil del navegador se conserva para reutilizar sesión viva"
    )
    cmd = " ".join(calls["cmd"])
    assert "nb_login_browser.py" in cmd, f"debe invocar al helper: {cmd}"
    assert "--profile-dir" in cmd and "--storage" in cmd, f"args del helper: {cmd}"
    # The profile dir passed must be the browser_profile sibling.
    bp = str(profile_dir / "browser_profile")
    assert bp in cmd.replace("\\", "/"), f"perfil incorrecto en el cmd: {cmd}"