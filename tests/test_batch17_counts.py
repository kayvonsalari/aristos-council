"""LIST-COUNTS-1 (Batch 17 item 13) — the list result's counts must agree with its own table.

Live (TM, GM, F, STLA, HMC, TSLA, two lenses): "shortlist: 3 on 1 of 2" sat above "Narrated 0 of 2
names that met the rule" on a run that never asked for narration, and "0 name(s) were ranked by ALL
2 lenses" sat under a table where Value + Momentum ranked 2 names and Magic Formula RAW ranked all
6. Findings, pinned below: the shortlist and the narration set are the SAME names (one definition),
the narration line is noise on a ranker-only run (now omitted), and "0" was the right count for
the wrong sentence - a lens left with fewer than 3 names to compare gives none of them a position,
so no rank-sum over both lenses exists; the sentence now says so.
"""
from __future__ import annotations

import copy

import pytest

import tests.test_multi_strategy_run as t
from aristos_council.data.adapter import Fundamentals
from aristos_council.pipeline import (comparable_names_line, narrated_union, narration_line,
                                      narration_was_requested, run_multi_strategy_pipeline)

IDS = ["magic_formula_momentum_v1", "magic_formula_raw_v1"]


def _funds(weak: bool) -> dict:
    base = copy.deepcopy(t._FUND)
    for n, ebit, pe in [("D", 800.0, 25.0), ("E", 2500.0, 12.0), ("F", 600.0, 30.0)]:
        d = copy.deepcopy(base["A"])
        d.update(ebit=[ebit], pe_ratio=pe, operating_income=[ebit] * 4,
                 pretax_income=[ebit * .95] * 4, tax_provision=[ebit * .2] * 4)
        base[n] = d
    if weak:                      # B, C, D, F fail Value + Momentum's return-on-capital floor
        for n in "BCDF":
            base[n].update(ebit=[50.0], operating_income=[50.0] * 4, pretax_income=[48.0] * 4,
                           tax_provision=[10.0] * 4)
    return base


def _run(weak: bool):
    funds = _funds(weak)

    class Adapter(t._Adapter):
        def get_fundamentals(self, ticker):
            if ticker == "DEAD":
                return Fundamentals(ticker="DEAD")
            return Fundamentals(ticker=ticker, name=ticker, **funds[ticker])

    return run_multi_strategy_pipeline(["A", "B", "C", "D", "E", "F"], IDS, adapter=Adapter(),
                                       strategies_dir=t.STRAT_DIR, today=t.TODAY)


def test_a_lens_left_with_two_names_places_none_and_the_sentence_says_so():
    res = _run(weak=True)
    assert res.meta["graded_by_all"] == 0                    # no name has BOTH lenses' positions
    vm = [r for r in res.results[IDS[0]].ranked if not r.excluded]
    raw = [r for r in res.results[IDS[1]].ranked if not r.excluded]
    assert (len(vm), len(raw)) == (2, 6)                     # the table the line sits under
    line = comparable_names_line(res)
    assert "0 name(s) were given a rank position by ALL 2 lenses" in line
    assert "kept only 2 names, too few (under 3) to give any a position" in line
    assert "ranked by ALL" not in line                       # the wording that contradicted it


def test_when_every_lens_places_the_names_the_count_is_the_intersection_and_no_aside():
    res = _run(weak=False)
    assert res.meta["graded_by_all"] == 4
    line = comparable_names_line(res)
    assert line.startswith("4 name(s) were given a rank position by ALL 2 lenses")
    assert "too few" not in line


@pytest.mark.parametrize("weak", [False, True])
def test_the_shortlist_and_the_narration_set_are_the_same_names(weak):
    res = _run(weak)
    ag = res.lens_agreement
    assert sorted(r.ticker for r in ag.rows) == sorted(narrated_union(res))
    # ...so the clause's name count adds up to the rows it summarises
    assert sum(ag.buckets().values()) == len(ag.rows)
    assert res.meta["narration"]["eligible"] == len(ag.rows)


def test_a_ranker_only_run_does_not_print_a_narration_line():
    res = _run(weak=True)
    plan = res.meta["narration"]
    assert plan["mode"] == "ranker" and not narration_was_requested(plan)
    from aristos_council.export.report_html import multi_strategy_report_html
    html = multi_strategy_report_html(res)
    assert "Narrated 0 of" not in html
    # a narrator run that narrated nothing still says so (NARR-ZERO-1)
    assert narration_was_requested({**plan, "mode": "narrator"})
    assert narration_line({**plan, "mode": "narrator"}).startswith("Narrated 0 of 2")


def test_the_markdown_and_screen_builders_omit_the_line_too():
    pytest.importorskip("streamlit")        # app.py needs the UI extra; CI installs none
    import app
    res = _run(weak=True)
    assert app._narration_line_markdown(res) == []


def test_a_ranker_only_record_counts_the_names_the_shortlist_shows(monkeypatch):
    """The live shape: "shortlist: 3 on 1 of 2" over "Narrated 0 of 2". The two sets differ when
    a lens left with fewer than 3 names still carries a raw BUY (the agreement table reads the
    verdict; ``narrated_union`` reads the grid cell, which says "too few to rank"). A ranker-only
    run narrates nothing, so what it records as "met the rule" is the shortlist the reader sees.
    Forced here by making the union disagree with the table."""
    import aristos_council.pipeline as pl

    real_union = pl.narrated_union
    monkeypatch.setattr(pl, "narrated_union", lambda result, coverage="buys_only": ["A"])
    res = _run(weak=True)
    monkeypatch.setattr(pl, "narrated_union", real_union)
    rows = [r.ticker for r in res.lens_agreement.rows]
    assert rows == ["A", "E"]                        # the table is unchanged: still 2 names
    record = res.meta["narration"]
    assert record["qualified"] == rows and record["eligible"] == len(rows) == 2


def test_a_narrating_runs_record_keeps_the_union_it_actually_narrates(monkeypatch):
    import aristos_council.pipeline as pl

    monkeypatch.setattr(pl, "narrated_union", lambda result, coverage="buys_only": ["A"])
    funds = _funds(weak=True)

    class Adapter(t._Adapter):
        def get_fundamentals(self, ticker):
            return Fundamentals(ticker=ticker, name=ticker, **funds[ticker])

    class Runners(dict):
        pass

    monkeypatch.setattr(pl, "_multi_narration_stage",
                        lambda *a, **k: ([], {"A": "narration"}, {}))
    res = run_multi_strategy_pipeline(["A", "B", "C", "D", "E", "F"], IDS, adapter=Adapter(),
                                      strategies_dir=t.STRAT_DIR, today=t.TODAY,
                                      ranker_only=False, runners=Runners())
    assert res.meta["narration"]["qualified"] == ["A"]      # what it narrated, not the table
