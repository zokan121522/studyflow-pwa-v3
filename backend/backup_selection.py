"""Selection model for the per-user backup.

The backup used to be all-or-nothing: one POST shipped the whole account,
which for a real user is over a gigabyte of generated media. This module
turns that into a question with checkboxes.

The core idea is TRANSITIVE CLOSURE. A selection is never just the ids the
user ticked — it is those ids plus everything that must travel with them for
the result to be restorable. A block without its topic, or a session without
its day, restores into a tree that does not hang together, so parents always
come along with children.

Two rules worth stating outright:

  * Generated media is attributed through `ai_tasks.topic_id`, which exists
    and is populated. It is NOT inferred from titles. A backup is either
    exact or it is not a backup; guessing which subject an MP3 belongs to
    and shipping it as though it were certain is the quietest possible way
    to hand someone a file of the wrong things.

  * `auth_tokens` is never exported, partial or otherwise.

The selector's default is a full backup. An absent or empty request body
must behave exactly as it did before this module existed, so `is_everything`
is the fallback everywhere rather than an error case.
"""
import re

# v3's media, which is not hub's. These are the five directories that hold
# generated or user-uploaded FILES, each with a table or an ai_tasks reference
# that can attribute them.
#
# Deliberately absent: `notebooklm`. That directory is not media -- it holds a
# Playwright browser profile per Google account (state.txt, config.json,
# profiles/<email>/), i.e. authenticated session material. It is not a thing
# to sweep into a ZIP that gets downloaded to a browser by default, so if it is
# ever wanted it has to be asked for on purpose, not inferred from a folder
# name.
MEDIA_CATEGORIES = ("pdf", "audio", "infographic", "image", "scraped_pdf")

# Which volume each category lives in, relative to the uploads root. Read by
# backup_files.py; kept next to the category list so adding a category means
# touching one place instead of two.
MEDIA_DIRS = {
    "pdf": "pdfs",
    "audio": "audio",
    "infographic": "infographics",
    "image": "images",
    "scraped_pdf": "scraped_pdfs",
}

# Which tables answer to which scope switch. "tree" is not a switch: it is
# driven by the id lists. Everything a user could reasonably want to leave
# out has to be named here, because anything unlisted would be exported
# regardless of what was unchecked — the exact failure mode this feature
# exists to prevent.
#
# Several of these have no table in v3 (flashcards, jsp, and most of
# agents_ai and preferences). They are left declared ON PURPOSE. Deleting
# them would make a missing schema indistinguishable from an empty scope, and
# the selector would show "0 filas" for a feature the user can actually see in
# the app. backup_db.resolve_tables intersects this list with the real schema
# and reports the difference into the manifest, so the UI can say which of
# the two it is.
SCOPE_TABLES = {
    "agenda": ("weeks", "days", "sessions", "custom_categories"),
    "habits": ("habit_columns", "habit_entries", "habit_notes"),
    "flashcards": ("flashcards_decks", "flashcards_cards", "match_scores",
                   "match_failed_cards"),
    "jsp": ("jsp_notebooks", "jsp_cells"),
    "agents_ai": ("agents", "agent_tools", "agent_scripts", "agent_skills",
                  "ai_tasks", "ai_usage_log", "chat_sessions",
                  "chat_messages", "notebooklm_owned_profiles"),
    "preferences": ("user_config", "user_addons", "column_visibility",
                    "personality_overrides", "custom_tools", "notices",
                    "planteamientos"),
}

# Shipped no matter what: the row that makes the account exist, and the
# personal miscellany that has no course of its own to be scoped against.
#
# v3 keeps its history in `quiz_results` (one row per answer, with
# question_id and block_id), where hub summarised it in `quiz_summaries`.
# There is no `quiz_summaries` here, so naming v3's own table is what keeps
# the attempt history out of the "unimported" list.
#
# Being absent from this list is worse than being in a scope the user
# unchecks. backup_user.guard() exports a table it does not recognise WITHOUT
# its selection filter, so an unclassified table means the user's checkbox had
# no effect on it -- the archive is complete and quietly wrong. That is why
# every v3 table carrying user_id appears either here or in SCOPE_TABLES.
# `pdf_annotations` follows the PDFs rather than riding along unconditionally;
# backup_resolve gives it a fragment for exactly that.
# `quiz_results` and `quiz_errors` carry course_id, topic_id AND block_id, all
# optional. Shipping them unconditionally is what hub did on the grounds that
# a wrong answer is small and belongs to the account. In v3 that breaks the
# export rather than merely bloating it: the rows reference courses the archive
# does not contain, so `psql -f` dies on a foreign key and the replayable-
# without-a-handful-of-fixes promise is void. They are scoped like everything
# else instead, which keeps the archive self-consistent -- a restore produces
# a tree with its own history and no dangling references. A question answered
# in a subject the user did not select is not in the backup, which is the
# same answer the user gave by not ticking that subject.
#
# Anything in this list must be self-contained: every foreign key it declares
# has to resolve inside the archive on its own.
ALWAYS_TABLES = ("users", "quick_notes", "todos", "user_settings",
                 "audio_files", "calendar_import_runs",
                 "calendar_import_notifications", "pdf_annotations")

# The tree the selector draws, in parent-to-child order.
#
# `questions` is kept under hub's name and resolved through
# backup_db.TABLE_RENAMES to v3's `quiz_questions`, which does have the
# `block_id` the tree query filters on. `cards` has no v3 counterpart yet; it
# stays declared so the manifest can say "flashcards are not backed up because
# this schema has no cards table" rather than pretending the user has none.
# quiz_results and quiz_errors join the tree because they hang off courses,
# topics and blocks by optional foreign key. See ALWAYS_TABLES for why they
# cannot simply be shipped unconditionally.
TREE_TABLES = ("courses", "topics", "blocks", "questions", "cards",
               "quiz_results", "quiz_errors")

# Selecting a day or a session has to work as well as selecting a week:
# the agenda is browsed day by day, and insisting on week-level ticks would
# make a user expand every week in the year to grab one afternoon.
ID_LISTS = ("courses", "topics", "blocks", "weeks", "days", "sessions")

_MAX_ID_LEN = 200
# NUL cannot live in a Postgres text column, and a newline would let a
# crafted id forge extra lines inside the COPY statement.
_FORBIDDEN_CHARS = ("\x00", "\n", "\r")


class Selection:
    """What the user asked for, normalised and validated.

    Constructed from the raw JSON body. A body that is absent, empty, or
    carries nothing usable yields `is_everything`, which is the historical
    behaviour.
    """

    def __init__(self, raw=None):
        raw = raw if isinstance(raw, dict) else {}
        self.raw = raw
        self.everything = self._looks_empty(raw)

        self.course_ids = _clean_ids(raw.get("courses"))
        self.topic_ids = _clean_ids(raw.get("topics"))
        self.block_ids = _clean_ids(raw.get("blocks"))
        self.week_ids = _clean_ids(raw.get("weeks"))
        self.day_ids = _clean_ids(raw.get("days"))
        self.session_ids = _clean_ids(raw.get("sessions"))

        if self.everything:
            self.scopes = set(SCOPE_TABLES)
            self.media = set(MEDIA_CATEGORIES)
            self.unattributed = True
        else:
            self.scopes = {s for s in _as_list(raw.get("scopes"))
                           if s in SCOPE_TABLES}
            self.media = {m for m in _as_list(raw.get("media"))
                          if m in MEDIA_CATEGORIES}
            self.unattributed = bool(raw.get("unattributed"))
            # "agenda" arrives as its own switch rather than a scope so the
            # selector can offer a week-level tree under it.
            if "agenda" in self.scopes:
                self.scopes.discard("agenda")
            if raw.get("agenda") is True or self.week_ids \
                    or self.day_ids or self.session_ids:
                self.scopes.add("agenda")

    @staticmethod
    def _looks_empty(raw: dict) -> bool:
        """True when the body asks for nothing specific, so take all."""
        if not raw:
            return True
        for key in ID_LISTS + ("scopes", "media"):
            if _as_list(raw.get(key)):
                return False
        for key in ("agenda", "unattributed"):
            if raw.get(key) is True:
                return False
        return True

    # ── predicates ────────────────────────────────────────────────

    @property
    def is_everything(self) -> bool:
        return self.everything

    @property
    def has_tree_pick(self) -> bool:
        return bool(self.course_ids or self.topic_ids or self.block_ids)

    @property
    def wants_media(self) -> bool:
        return bool(self.media)

    def scope_on(self, name: str) -> bool:
        return self.everything or name in self.scopes

    def media_on(self, name: str) -> bool:
        return self.everything or name in self.media

    def as_manifest(self) -> dict:
        """Exactly what was asked for, so the restore can say so out loud."""
        if self.everything:
            return {"mode": "everything"}
        return {
            "mode": "partial",
            "courses": sorted(self.course_ids),
            "topics": sorted(self.topic_ids),
            "blocks": sorted(self.block_ids),
            "weeks": sorted(self.week_ids),
            "days": sorted(self.day_ids),
            "sessions": sorted(self.session_ids),
            "scopes": sorted(self.scopes),
            "media": sorted(self.media),
            "unattributed": self.unattributed,
        }


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, (str, int, float))]
    return []


def _clean_ids(value) -> set:
    """Collect the ids, rejecting only what Postgres could not have stored.

    These strings end up inside SQL, so safety comes from escaping
    (`sql_text`), not from filtering. Filtering was the earlier approach
    and it was wrong for this schema: primary keys here are readable
    slugs — `general-BaseDeDatos  🗃️` is a real topic id — and a
    `[A-Za-z0-9_.:-]` whitelist threw them away. The selection then looked
    empty, `is_everything` stayed false, and the backup shipped nothing at
    all with no error anywhere.

    What remains worth rejecting: empty or absurdly long strings, and
    control characters. A literal NUL cannot be in a text column, so
    refusing it here costs nothing.
    """
    out = set()
    for item in _as_list(value):
        text = str(item).strip()
        if not text or len(text) > _MAX_ID_LEN:
            continue
        if any(ch in text for ch in _FORBIDDEN_CHARS):
            continue
        out.add(text)
    return out
