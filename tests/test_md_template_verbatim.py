"""The "fiel al original" NotebookLM template must exist and stay verbatim.

The user asked for a template that puts the PDF "exactamente igual" in
Markdown so they can work on it. That is the opposite of every other
template in the catalog: they are all *transformations* (summarize,
restructure, enrich). This one is a transcription.

Two invariants are asserted here:

1. The template is reachable — catalog entry, role, and the id used by the
   frontend all agree. A missing role would silently fall back to
   'notas-estandar', which is exactly the summary behaviour the user
   rejected, so the test fails rather than degrading silently.
2. The prompt still forbids the behaviours that would break faithfulness.
   An LLM prompt is easy to "improve" into a summary by accident; these
   assertions make that a test failure.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ai.notebooklm.md_templates import _ROLES_BY_ID  # noqa: E402
from ai.notebooklm.md_templates_catalog import MD_TEMPLATES  # noqa: E402

TEMPLATE_ID = "transcripcion-fiel"


def test_role_exists():
    assert TEMPLATE_ID in _ROLES_BY_ID, "el prompt de la plantilla no existe"


def test_catalog_entry_exists_and_is_complete():
    entry = MD_TEMPLATES.get(TEMPLATE_ID)
    assert entry is not None, "no aparece en el catalogo (no seria seleccionable)"
    for key in ("name", "emoji", "description", "mock"):
        assert entry.get(key), f"falta '{key}' en la entrada de catalogo"


def test_catalog_and_roles_agree_in_both_directions():
    """Every catalog id must resolve to a role, and every role be offered."""
    assert set(MD_TEMPLATES) == set(_ROLES_BY_ID), (
        "desincronizado: "
        f"solo en catalogo={set(MD_TEMPLATES) - set(_ROLES_BY_ID)} "
        f"solo en roles={set(_ROLES_BY_ID) - set(MD_TEMPLATES)}"
    )


def test_role_forbids_summarising():
    role = _ROLES_BY_ID[TEMPLATE_ID].lower()
    # It must forbid summarising, not merely avoid mentioning it.
    for phrase in ("summar", "resum", "paraphras", "takeaway"):
        assert phrase in role, f"la plantilla deja de prohibir '{phrase}'"


def test_role_forbids_adding_material():
    role = _ROLES_BY_ID[TEMPLATE_ID].lower()
    assert "no added material" in role, "debe prohibir anadir material ausente"
    assert "verbatim" in role, "debe exigir literalidad"


def test_role_handles_unreadable_without_guessing():
    role = _ROLES_BY_ID[TEMPLATE_ID]
    assert "[ilegible]" in role, "debe marcar lo ilegible en vez de inventar"
    assert "never guess" in role.lower(), "debe prohibir adivinar"


def test_role_preserves_code_verbatim():
    role = _ROLES_BY_ID[TEMPLATE_ID].lower()
    assert "code" in role, "debe tratar el codigo"
    assert "refactor" in role, "debe prohibir reescribir el codigo de origen"


def test_role_marks_figures_instead_of_dropping_them():
    role = _ROLES_BY_ID[TEMPLATE_ID]
    assert "figura" in role.lower(), "debe marcar las figuras en su sitio"
    assert "page markers" in role.lower() or "<!-- p." in role, (
        "debe anclar el markdown a las paginas del PDF"
    )


def test_no_preamble_so_the_output_is_usable_directly():
    role = _ROLES_BY_ID[TEMPLATE_ID].lower()
    assert "no preamble" in role, (
        "el markdown debe empezar en el contenido, no con un prologo que "
        "habria que borrar antes de trabajar con el"
    )


def test_existing_templates_are_untouched():
    """Guard against an accidental rewrite while adding the new one."""
    for tid in (
        "notas-estandar",
        "transcripcion",
        "tutorial",
        "comparativa",
        "glosario",
        "faq",
    ):
        assert tid in _ROLES_BY_ID, f"la plantilla existente {tid} desaparecio"
        assert tid in MD_TEMPLATES, f"la plantilla existente {tid} salio del catalogo"


def test_unknown_template_still_falls_back_to_standard():
    """The fallback path must keep working for typos / stale ids."""
    from ai.notebooklm.md_templates import get_md_template_role

    assert get_md_template_role(None) is None
    assert get_md_template_role("no-existe") is None
    assert get_md_template_role(TEMPLATE_ID) is not None


def test_role_has_no_python_format_placeholders():
    """Roles are concatenated into prompts; a stray {} would break compose."""
    for tid, role in _ROLES_BY_ID.items():
        assert not re.search(r"\{[a-zA-Z_]", role), (
            f"la plantilla {tid} tiene un placeholder sin formatear"
        )