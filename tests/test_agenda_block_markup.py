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


def _first_child_at(body):
    """Index of the first child element of the block, or -1 if absent."""
    for marker in ('<div class="tl-block-bar"', '<div class="tlw-block-bar"'):
        at = body.find(marker)
        if at != -1:
            return at
    return -1


def test_dia_cierra_la_etiqueta_con_ela_del_style():
    """The exact regression: the day block must close its tag with `;">`.

    Before the fix the line ended `';"'`, so the `<div class="tl-block">` was
    never terminated and the parser consumed the following markup as bogus
    attributes, emptying every block. Pinning the literal is the cheapest
    guard that cannot itself be fooled by refactors of the builder.
    """
    body = DAY_JS.read_text(encoding="utf-8")
    assert "'border-left-color:' + color + ';\">' +" in body, (
        "el div del bloque del dia debe cerrarse con ';\">' (falta el '>')"
    )
    assert "'border-left-color:' + color + ';\"' +" not in body, (
        "vuelve el bracket perdido: el div no se cierra y el contenido se expulsa"
    )


def test_semana_cierra_la_etiqueta_tras_el_title():
    """The week block appends a title attribute, so the tag closes there.

    It used to close the style early *and* leave the `>` on the title line,
    which printed `title="...">` as visible text — the "looks like embedded
    code" symptom.
    """
    body = WEEK_JS.read_text(encoding="utf-8")
    assert "'border-left-color:' + color + ';\"' +" in body
    assert "' title=\"' + (session.title || \"Sesión\") + ' · ' + timeStr + '\">' +" in body, (
        "el title de la semana debe terminar la etiqueta con '>'"
    )
    assert "'border-left-color:' + color + ';\">' +" not in body, (
        "no se debe cerrar la etiqueta antes del title: dejaria un '>' visible"
    )
    assert " · " in body  # el title conserva el separador legible


def test_el_dia_no_lleva_title_espurio():
    """The day block shows its title in the body, not as a title attribute."""
    body = DAY_JS.read_text(encoding="utf-8")
    assert '<div class="tl-block-body">' in body
    assert 'class="tl-block-title"' in body
    assert 'class="tl-block-time"' in body
    assert " · " not in body, (
        "el bloque del dia no debe llevar un title con punto medio"
    )


def test_html_de_las_dos_vistas_esta_balanceado():
    """renderBlock opens and closes exactly the divs it declares."""
    for path, fn in ((DAY_JS, "renderBlock"), (WEEK_JS, "renderWeekBlock")):
        body = render_block_source(path, fn)
        opens = len(re.findall(r"<div\b", body))
        closes = len(re.findall(r"</div>", body))
        assert opens == closes, (
            "%s/%s abre %d divs y cierra %d" % (path.name, fn, opens, closes)
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
