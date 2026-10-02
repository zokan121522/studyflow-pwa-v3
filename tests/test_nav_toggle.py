"""Regressions for the header nav toggle (button next to the API pill).

Every assertion here is about something that actually broke once:

- The button sat inside .header-logo, so its click bubbled to the logo
  handler, which navigates to the dashboard and hides the whole main area.
- The focus-mode CSS only targeted #agenda-left, so collapsing the studyflow
  nav did nothing at all.
- The centre column stayed 340px wide because .main is a *grid* and the
  original `flex: 1 1 100%` is inert in a grid container.
- The button icon did not follow ⌘/Ctrl+B, because the observer only watched
  childList and the class toggle is an attribute change.
"""

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
INDEX = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
AGENDA_CSS = (FRONTEND / "features" / "agenda" / "agenda.css").read_text(
    encoding="utf-8"
)

BUTTON_ID = "nav-toggle-btn"


def _body_of(html: str, marker: str) -> str:
    """Return the markup from ``marker`` up to the next `</div>`."""
    start = html.index(marker)
    return html[start : html.index("</div>", start)]


class TestButtonIsNotInsideTheLogo:
    """A click on the button must not reach the .header-logo handler."""

    def test_button_lives_outside_the_logo_element(self):
        logo = _body_of(INDEX, '<div class="header-logo">')
        assert f'id="{BUTTON_ID}"' not in logo, (
            "nav toggle is nested inside .header-logo; its click bubbles to the "
            "logo handler and navigates to the dashboard"
        )

    def test_click_handler_stops_propagation(self):
        handler = APP_JS[APP_JS.index("navToggle.addEventListener") :]
        handler = handler[: handler.index("});")]
        # Must be a real call, not the word inside a comment — otherwise
        # commenting the line out would still pass.
        assert re.search(
            r"^\s*e\.stopPropagation\(\);", handler, re.MULTILINE
        ), "nav toggle click must actually call stopPropagation()"


class TestFocusModeHidesBothViews:
    """Both panels are the same shape under different ids, so both need rules."""

    @pytest.mark.parametrize("nav_id", ["agenda-left", "studyflow-left"])
    def test_nav_column_is_hidden(self, nav_id):
        assert re.search(
            rf"\.main\.sf-focus-mode\s+#{re.escape(nav_id)}\s*\{{[^}}]*display:\s*none",
            AGENDA_CSS,
        ), f"focus mode never hides #{nav_id}"

    @pytest.mark.parametrize("center_id", ["agenda-center", "studyflow-center"])
    def test_center_column_is_claimed(self, center_id):
        assert re.search(rf"\.main\.sf-focus-mode\s+#{re.escape(center_id)}\s*\{{", AGENDA_CSS)


class TestFocusModeCollapsesTheGrid:
    """.main is a grid, so flex properties do nothing — the grid must collapse."""

    def test_focus_mode_sets_a_single_grid_track(self):
        assert re.search(
            r"\.main\.sf-focus-mode\s*\{[^}]*grid-template-columns:\s*1fr",
            AGENDA_CSS,
        ), (
            "focus mode must collapse .main to a single grid track; otherwise "
            "the centre auto-places into the 340px left track and stays narrow"
        )

    def test_main_is_not_being_driven_as_a_flex_container(self):
        # Guards against reintroducing flex-only rules for this effect.
        focus_block = AGENDA_CSS[
            AGENDA_CSS.index(".main.sf-focus-mode {") : AGENDA_CSS.index(
                ".main.sf-focus-mode #agenda-left"
            )
        ]
        assert "grid-template-columns" in focus_block
        assert "display: flex" not in focus_block


class TestInitialStateIsExpanded:
    """The nav must never come up collapsed."""

    def test_focus_mode_is_removed_on_startup(self):
        block = APP_JS[APP_JS.index("const navToggle") :]
        block = block[: block.index("navToggle.addEventListener")]
        assert re.search(r"classList\.remove\(['\"]sf-focus-mode['\"]\)", block), (
            "startup must clear sf-focus-mode so the nav always starts visible"
        )

    def test_state_is_not_persisted(self):
        block = APP_JS[APP_JS.index("const navToggle") :]
        block = block[: block.index("\n  });")]
        assert "localStorage" not in block, (
            "a persisted hidden state would survive a reload and violate the "
            "always-start-visible rule"
        )


class TestButtonStateStaysInSync:
    def test_observer_watches_class_attributes(self):
        obs = APP_JS[APP_JS.index("observer.observe") :]
        obs = obs[: obs.index("sync();")]
        assert "attributes: true" in obs, (
            "⌘/Ctrl+B changes an attribute, not childList; without this the "
            "button icon goes stale"
        )
        assert "attributeFilter" in obs, (
            "restrict the attribute watch to 'class' to keep it cheap"
        )

    def test_sync_only_writes_on_a_real_change(self):
        sync = APP_JS[APP_JS.index("const sync = () => {") :]
        sync = sync[: sync.index("navToggle.addEventListener")]
        for attr in ("textContent", "aria-pressed", "aria-label"):
            assert f"navToggle.{attr} !==" in sync or f"getAttribute('{attr}')" in sync, (
                f"sync() must compare {attr} before writing, or it retriggers "
                "its own observer forever and the app never finishes booting"
            )
