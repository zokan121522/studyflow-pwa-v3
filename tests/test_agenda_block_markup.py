"""Regression tests for well-formed block markup in the timeline views.

`renderBlock` used to close the style attribute as `;"` instead of `;">`, so
the opening `<div class="tl-block">` was never terminated. The HTML parser
then swallowed the following markup as bogus attributes on that div
(a stray `<div="">` appeared) and hoisted `.tl-block-body` out to whatever
level it could, leaving every block visually empty and the session labels
stacked under the grid.

The views are built by string concatenation, so nothing but a test catches a
missing bracket: the file parses fine and the render call never throws.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "features" / "agenda"
DAY_JS = ROOT / "agenda-timeline.js"
WEEK_JS = ROOT / "agenda-timeline-week.js"


def render_block_source(path, fn_name):
    """Extract the body of a `function fnName(...)` from the module source."""
    text = path.read_text(encoding="utf-8")
    start = text.index("function %s(" % fn_name)
    depth, i = 0, text.index("{", start)
    begin = i
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[begin : i + 1]
        i += 1
    raise AssertionError("no se pudo extraer %s de %s" % (fn_name, path.name))


@pytest.mark.parametrize(
    "path,fn_name",
    [(DAY_JS, "renderBlock"), (WEEK_JS, "renderWeekBlock")],
    ids=["dia", "semana"],
)
def test_bloque_cierra_la_etiqueta_de_apertura(path, fn_name):
    """The block's opening div must be terminated with `>` after the style."""
    body = render_block_source(path, fn_name)
    offenders = [
        line.strip()
        for line in body.splitlines()
        if re.search(r"'\s*;\"'\s*\+", line)
    ]
    assert not offenders, (
        "%s: el div del bloque no se cierra (falta '>' tras el style). Eso hace "
        "que el parser expulse el contenido y el bloque salga vacío: %s"
        % (path.name, offenders)
    )


@pytest.mark.parametrize(
    "path,fn_name",
    [(DAY_JS, "renderBlock"), (WEEK_JS, "renderWeekBlock")],
    ids=["dia", "semana"],
)
def test_bloque_emite_una_sola_etiqueta_style(path, fn_name):
    """Exactly one `style="` opens the block, and it is closed properly."""
    body = render_block_source(path, fn_name)
    opening = [ln.strip() for ln in body.splitlines() if "' style=\"" in ln]
    assert len(opening) == 1, (
        "%s: se esperaba un unico style de apertura en el bloque, hay %d"
        % (path.name, len(opening))
    )
    assert any("';\">'" in ln for ln in body.splitlines()), (
        "%s: el style del bloque debe terminar en ';\">' para cerrar la etiqueta"
        % path.name
    )


def test_el_bloque_del_dia_no_tiene_title_sobrante():
    """The day block carries its own title markup; no stray title attribute."""
    body = render_block_source(DAY_JS, "renderBlock")
    assert "<div class=\"tl-block-body\">" in body
    assert 'class="tl-block-title"' in body
    assert 'class="tl-block-time"' in body
    assert " · " not in body, (
        "el bloque del dia no debe llevar un title con punto medio: el dia "
        "muestra el titulo en el cuerpo, no en un atributo title"
    )


def test_html_del_dia_esta_balanceado():
    """renderBlock opens and closes exactly the divs it declares."""
    body = render_block_source(DAY_JS, "renderBlock")
    opens = len(re.findall(r"<div\b", body))
    closes = len(re.findall(r"</div>", body))
    assert opens == closes, (
        "renderBlock abre %d divs y cierra %d: el HTML no esta balanceado"
        % (opens, closes)
    )
