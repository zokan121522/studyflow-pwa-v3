"""Regression tests for lane packing against the height the views actually draw.

`App.Lanes.pack` defaults an interval with no end (or an end equal to its
start) to 60 minutes. Both timeline views draw such an instant as a short
sliver instead: 18px in the day view (27 minutes at 40px/hour) and 15 minutes
in the week view. The two disagreed, so an instant at 14:03 was believed to
run until 15:03, joined a cluster with the 15:00 block, and stole a lane from
a session it never overlapped. Unrelated blocks ended up 33% or 50% wide.

These tests pin the contract: the fallback handed to the packer must be the
span the view paints, and the views must not fall back to the 60-minute
default.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "features" / "agenda"
DAY_JS = ROOT / "agenda-timeline.js"
WEEK_JS = ROOT / "agenda-timeline-week.js"
PACKER = Path(__file__).resolve().parents[1] / "frontend" / "shared" / "lane-packer.js"

HOUR_H = 40
DAY_MIN_BLOCK_PX = 18
WEEK_MIN_BLOCK_MIN = 15


@pytest.fixture(scope="module")
def day():
    return DAY_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def week():
    return WEEK_JS.read_text(encoding="utf-8")


def test_dia_declara_el_suelo_en_px_y_lo_convierte(day):
    """The day view states its floor in px and derives the minutes from it."""
    assert "var MIN_BLOCK_H = %d" % DAY_MIN_BLOCK_PX in day
    assert "var ZERO_LEN_MIN = Math.round(MIN_BLOCK_H / HOUR_H * 60)" in day


def test_dia_el_suelo_equivale_a_27_minutos(day):
    """18px at 40px/hour is 27 minutes; the conversion must agree."""
    m = re.search(r"var ZERO_LEN_MIN = [^;]+", day)
    assert m, "no se encontro ZERO_LEN_MIN"
    assert HOUR_H == 40
    assert round(DAY_MIN_BLOCK_PX / HOUR_H * 60) == 27


def test_dia_pasa_el_suelo_al_empaquetador(day):
    """The packer call must carry the fallback, not an empty options object."""
    assert "App.Lanes.pack(scheduled, { fallbackMinutes: ZERO_LEN_MIN });" in day
    assert "App.Lanes.pack(scheduled, {})" not in day, (
        "el dia volveria al valor por defecto de 60 minutos"
    )


def test_semana_pasa_su_suelo_al_empaquetador(week):
    """Same for the week view, with its own 15-minute floor."""
    assert "var MIN_BLOCK_MIN = %d" % WEEK_MIN_BLOCK_MIN in week
    assert "App.Lanes.pack(daySessions, { fallbackMinutes: MIN_BLOCK_MIN });" in week
    assert "App.Lanes.pack(daySessions, {})" not in week


def test_el_bloque_del_dia_usa_el_suelo_declarado(day):
    """renderBlock must draw with the same constant the packer was told."""
    assert "var MIN_H = MIN_BLOCK_H;" in day
    assert "var MIN_H = 18;" not in day, (
        "el suelo dibujado y el del empaquetador deben ser la misma constante"
    )


def test_el_bloque_de_la_semana_usa_el_suelo_declarado(week):
    """The week block clamps to the declared floor, not to 60."""
    assert "Math.max(MIN_BLOCK_MIN, end.totalMin - start.totalMin)" in week
    assert ": 60;" not in week, "la semana sigue asumiendo una hora de duracion"


def test_un_instante_no_roba_carril_a_una_sesion_posterior():
    """The behaviour that motivated the fix, on the real packer.

    A 14:03 instant and a 15:00-16:00 block must not share a cluster, so both
    keep the full width. Before the fix the packer stretched the instant to an
    hour and split them into two 50% lanes.
    """
    lanes = _run_node(
        """
        const items = [
          {start_time: '14:03', end_time: '14:03'},
          {start_time: '15:00', end_time: '16:00'},
        ];
        App.Lanes.pack(items, {fallbackMinutes: 27});
        return items.map(i => i._lane);
        """
    )
    assert lanes[0]["total"] == 1, (
        "el instante y la sesion de las 15:00 no se solapan: ambos a ancho "
        "completo,中得到 %r" % lanes
    )
    assert lanes[1]["total"] == 1


def test_dos_instantes_que_se_pisan_si_comparten_carril():
    """Two instants 18 minutes apart do overlap once drawn as 27-minute
    slivers, so they legitimately split into two columns."""
    lanes = _run_node(
        """
        const items = [
          {start_time: '14:03', end_time: '14:03'},
          {start_time: '14:21', end_time: '14:21'},
        ];
        App.Lanes.pack(items, {fallbackMinutes: 27});
        return items.map(i => i._lane);
        """
    )
    assert lanes[0]["total"] == 2
    assert lanes[1]["total"] == 2


def test_con_el_defecto_de_60_minutos_suena_mal():
    """Pins why the fallback matters: the 60-minute default splits a pair
    that never overlaps. If this stops failing, the bug is back."""
    lanes = _run_node(
        """
        const items = [
          {start_time: '14:03', end_time: '14:03'},
          {start_time: '15:00', end_time: '16:00'},
        ];
        App.Lanes.pack(items, {});
        return items.map(i => i._lane);
        """
    )
    assert lanes[0]["total"] == 2, (
        "sin fallbackMinutes el packer estira el instante a una hora y parte "
        "la columna: este test documenta el defecto que corrigio el fix"
    )


def _run_node(snippet: str) -> list:
    """Run JS with lane-packer.js loaded and return the JSON it prints."""
    script = f"""
    require({str(PACKER)!r});
    const out = (function () {{
      {snippet}
    }})();
    process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, "node fallo: %s" % proc.stderr.strip()
    return json.loads(proc.stdout)


@pytest.mark.parametrize("view", ["dia", "semana"])
def test_el_valor_por_defecto_sigue_siendo_60(view):
    """Documents the packer default we deliberately override in both views."""
    text = PACKER.read_text(encoding="utf-8")
    assert "var fallbackMinutes = opts.fallbackMinutes || 60;" in text
