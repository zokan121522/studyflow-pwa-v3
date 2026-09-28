# backend/addons_seed.py
"""Addon catalog seed (Sub-phase SA — Addons Foundation).

Rows inserted/upserted by `seed_addon_catalog()` at startup. Adapted from
v2's `studyflow-hub/tierra/backend/models/addons_seed.py` for the v3
single-user PWA:

  • No `service_name` / `port` / legacy-prefix proxy fields — v3 has no
    sidecar Docker services. `url_prefix` is kept informational only.
  • `installed`, `enabled`, `hidden` are NEVER touched by the upsert
    conflict path, so user choices survive re-seeding (mirrors v2's
    "status preserved across re-seed" comment).
  • `hidden` IS seeded on the initial INSERT for IA-bound slugs so they
    stay tucked away until the IA backend ships.

Catalog adapted to v3 reality: S5/S6/S9 addons (quiz/flashcards/playground)
are present so the marketplace shows them up-front, but their JS modules
are deferred — they install cleanly the moment those phases land.

NOTE: kept flat at backend/addons_seed.py (not in models/) because v3 has
backend/models.py as a single file, not a package.
"""

# AI-bound slugs are seeded hidden=True so the marketplace filters them away
# by default. All other slugs are visible (hidden=False on initial INSERT).
HIDDEN_BY_DEFAULT = frozenset({"notebooklm", "opencode"})

ADDON_CATALOG_SEED = [
    # ── Quiz (S5) ────────────────────────────────────────────────
    {
        "slug": "quiz",
        "name": "Quiz",
        "description": "Autoevaluación tipo test por tema (S5 — pendiente).",
        "version": "0.1.0",
        "url_prefix": "/api/quiz",
    },
    # ── Flashcards (S6) ──────────────────────────────────────────
    {
        "slug": "flashcards",
        "name": "Flashcards",
        "description": "Tarjetas de estudio estilo Quizlet + modo manual + IA (S6 — pendiente).",
        "version": "0.1.0",
        "url_prefix": "/api/flashcards",
    },
    # ── Code Playground (S9) ─────────────────────────────────────
    {
        "slug": "jsplayground",
        "name": "Code Playground",
        "description": "Notebooks interactivos JS/Python/Java con kernel persistente (S9 — pendiente).",
        "version": "0.3.0",
        "url_prefix": "/api/jsp",
    },
    # ── English Grammar Exercises (Phase 61 v2 port) ─────────────
    {
        "slug": "english",
        "name": "English",
        "description": "Ejercicios de gramática inglesa generados con OpenZen local desde bloques Markdown.",
        "version": "1.0.0",
        "url_prefix": "/api/ai/generate-grammar-exercises",
    },
    # ── IA phase addons (hidden until the IA backend ships) ──────
    {
        "slug": "notebooklm",
        "name": "NotebookLM",
        "description": "Generación de contenido con Gemini (Markdown, HTML, Test, Infografía) — fase IA, pendiente.",
        "version": "1.0.0",
        "url_prefix": "/api/ai/notebooklm",
    },
    {
        "slug": "opencode",
        "name": "OpenZen",
        "description": "Agente local sin cuota (OpenZen, Audio, YouTube) — fase IA, pendiente.",
        "version": "1.18.27",
        "url_prefix": "",
    },
    # ── Core toolbar sections (Phase 59 v2 — always visible) ─────
    {
        "slug": "core",
        "name": "Núcleo",
        "description": "Núcleo del bloque: SCORM y acciones base siempre disponibles.",
        "version": "1.0.0",
        "url_prefix": "",
    },
    {
        "slug": "add",
        "name": "Añadir",
        "description": "Sección Añadir del bloque: creación de bloques (web, ejercicio, markdown, PDF).",
        "version": "1.0.0",
        "url_prefix": "",
    },
]


def seed_addon_catalog() -> None:
    """Idempotent upsert of the addon catalog.

    Inserts a fresh row per slug with `hidden` set from HIDDEN_BY_DEFAULT;
    on CONFLICT (slug) the UPDATE branch refreshes ONLY name/description/
    version/url_prefix so user choices (installed/enabled/hidden) survive
    re-seeding. Mirrors v2's "status preserved across re-seed" rule.
    """
    from backend.database import execute

    for addon in ADDON_CATALOG_SEED:
        execute(
            """
            INSERT INTO addons (slug, name, description, version,
                                url_prefix, hidden)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description,
                version     = EXCLUDED.version,
                url_prefix  = EXCLUDED.url_prefix,
                updated_at  = NOW()
            """,
            (
                addon["slug"],
                addon["name"],
                addon.get("description", ""),
                addon.get("version", "0.0.0"),
                addon.get("url_prefix"),
                addon["slug"] in HIDDEN_BY_DEFAULT,
            ),
        )