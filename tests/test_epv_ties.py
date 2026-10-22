"""EPV-TIES-1 - Earnings Power Value no longer collapses every negative-margin company onto one rank.

Live, 2026-10-22 sweep: NVCR's peers had 6 names tied 18th of 23 and VKTX's 5 tied 3rd of 7 on a ONE-factor
lens. The cause: a company whose five-year average operating margin is negative got no value ("nothing to
capitalise") and ``missing: worst`` put them all on one shared last rank - a floor. In the frozen VKTX group,
9969.HK (average margin -58.6%) and VCYT (-3.6%) sat tied. They now get a margin of safety below -100%, still
below every company with positive earning power, ordered by how far below zero.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from aristos_council import factors as F
from aristos_council.persistence.replay import FrozenAdapter

FIX = Path(__file__).resolve().parent / "fixtures" / "report_sweep"
TODAY = date.fromisoformat(json.loads((FIX / "meta.json").read_text())["recorded_on"])


def _mos(ticker):
    fi = F.gather_factor_inputs(FrozenAdapter(FIX / "frozen"), ticker, today=TODAY)
    return F.epv_reading_for(fi)


def test_the_two_vktx_peers_that_tied_now_have_distinct_values_in_the_right_order():
    hk, vcyt, camx = _mos("9969.HK"), _mos("VCYT"), _mos("CAMX.ST")
    assert hk.margin_of_safety is not None and vcyt.margin_of_safety is not None
    assert hk.margin_of_safety != vcyt.margin_of_safety
    assert hk.margin_of_safety < vcyt.margin_of_safety < -1.0 < camx.margin_of_safety
    assert "negative or zero" in hk.note and "-58.6%" in hk.note


def test_on_the_vktx_page_they_no_longer_share_a_rank():
    import tempfile
    from tests.test_report_sweep import build_sweep
    rep = build_sweep(Path(tempfile.mkdtemp()))["company VKTX"]["company"]
    ranked = {r["ticker"]: r for r in rep.lens_ranks["epv_v1"]["ranked"]}
    assert ranked["VCYT"]["position"] == 2 and ranked["9969.HK"]["position"] == 3
