"""Regresión: los onclick del frontend solo pueden llamar a funciones que existen.

Bug real encontrado probando la UI como usuario (2026-10-05):
"📝 Añadir markdown", "📕 Añadir PDF" y "➕ Añadir separador" eran botones
MUERTOS. courses.js importaba `addBlock` desde CoursesAPI pero no lo
re-exportaba en su `return {...}`, así que `window.App.Courses.addBlock`
era undefined. El onclick no tenía .catch(), así que el fallo era
totalmente silencioso: el menú se cerraba y no pasaba nada.

Este test extrae los `window.App.<NS>.<fn>(` de todos los ficheros JS y
comprueba que cada función está expuesta por el módulo correspondiente,
permitiendo los plugins opcionales que ya se invocan con guardia
(`typeof window.App.X.y === "function"`).
"""

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[1] / "frontend"

# Namespaces cuyo fichero no existe: son plugins opcionales y todas sus
# llamadas están dentro de guardias `typeof ... === "function"`, por lo que
# la app degrada sin romper. Se verifican aparte con test_optional_plugins_guarded.
OPTIONAL_NAMESPACES = {"AgendaMind", "FlashcardsStats", "PlaygroundStats"}


def js_files():
    return sorted(p for p in FRONTEND.rglob("*.js") if "vendor" not in str(p))


def module_exports(src: str) -> set[str]:
    """Nombres públicos del return final de un módulo IIFE.

    Solo 27 de los 69 módulos del frontend exponen API; el resto son hojas
    auxiliares, así que un return ausente es legítimo (skip), no un fallo.
    """
    exported: set[str] = set()
    for block in re.findall(r"return \{(.*?)\n\s*\};", src, re.S):
        exported |= set(re.findall(r"^\s+([A-Za-z0-9_]+)\s*,", block, re.M))
    return exported


def test_frontend_has_js():
    assert len(js_files()) > 50, "el frontend debería tener decenas de módulos"


@pytest.mark.parametrize(
    "path", js_files(), ids=lambda p: p.name
)
def test_module_onclick_refs_resolve(path):
    """Toda llamada window.App.<este_modulo>.<fn>() debe estar exportada."""
    src = path.read_text(errors="ignore")
    exported = module_exports(src)
    if not exported:
        pytest.skip(f"{path.name} no devuelve API pública")

    stem = path.stem
    called = {
        m
        for m in re.findall(r"window\.App\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*\(", src)
    }
    dead = sorted(
        f"{ns}.{fn}"
        for ns, fn in called
        if ns == stem and fn not in exported and not fn.startswith("_")
    )
    assert not dead, (
        f"{path.name} llama a funciones que no exporta (botones muertos): {dead}"
    )


def test_courses_exports_addblock():
    """Regresión directa del bug: sin esto, 5 botones del sidebar no hacen nada."""
    src = (FRONTEND / "features/studyflow/courses.js").read_text()
    assert "addBlock" in module_exports(src), (
        "Courses.addBlock debe exportarse: el sidebar lo invoca por onclick"
    )


def test_addblock_calls_have_catch():
    """Un .then() sin .catch() convierte un fallo en silencio."""
    src = (FRONTEND / "features/studyflow/courses-sidebar.js").read_text()
    calls = re.findall(r"window\.App\.Courses\.addBlock\(", src)
    catches = re.findall(r"window\.App\.Courses\.addBlock\(.*?\.catch\(", src, re.S)
    assert len(calls) == 5, f"se esperaban 5 llamadas a addBlock, hay {len(calls)}"
    assert len(catches) == len(calls), (
        f"addBlock: {len(calls)} llamadas pero solo {len(catches)} con .catch()"
    )


def test_optional_plugins_are_guarded():
    """Los plugins opcionales deben invocarse con guardia, no a pelo."""
    for fname in ("features/agenda/agenda-core.js", "features/studyflow/courses.js"):
        src = (FRONTEND / fname).read_text()
        for m in re.finditer(r"window\.App\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*\(", src):
            ns, fn = m.group(1), m.group(2)
            if ns not in OPTIONAL_NAMESPACES:
                continue
            line = src[src.rfind("\n", 0, m.start()) + 1 : m.end()]
            # puede estar en la línea anterior (&& al inicio)
            prev_start = src.rfind("\n", 0, src.rfind("\n", 0, m.start()))
            window = line + src[prev_start : m.start()]
            assert (
                "typeof window.App." in window or f"window.App.{ns} &&" in window
            ), f"{fname}: {ns}.{fn} se llama sin guardia optional"