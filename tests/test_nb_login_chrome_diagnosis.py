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


def test_login_route_still_requests_desktop_chrome():
    """The --browser chrome flag is the contract with the library."""
    assert '"--browser", "chrome"' in _source(), (
        "el login debe seguir pidiendo Chrome de escritorio; cambiarlo a "
        "chromium cambiaría el comportamiento del login"
    )


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