"""FACTOR-MARK-1 — say when a rank stands on fewer factors than the lens has.

Under an imputing policy (``missing: neutral``) a name short a factor is scored from the
ranks it does have. That is the right call — judging a name on what it has beats dumping it
for a gap — and it is invisible: Forensic ranked 39 of 106 oil names with no Altman Z, and
those cells read exactly like fully measured ones.

Display only. Nothing here changes a rank, a verdict or a score.
"""

from __future__ import annotations

from aristos_council.pipeline import MultiStrategyCell


def _cell(**kw):
    base = dict(strategy_id="x_v1", status="ranked", position=3, cohort_size=10,
                verdict="buy")
    base.update(kw)
    return MultiStrategyCell(**base)


def test_a_name_missing_a_factor_carries_the_marker():
    cell = _cell(factors_total=3, factors_measured=2)
    assert cell.factor_note == " · ranked on 2 of 3 factors"
    assert cell.render() == "#3 of 10 · BUY · ranked on 2 of 3 factors"


def test_a_fully_measured_name_carries_nothing():
    cell = _cell(factors_total=3, factors_measured=3)
    assert cell.factor_note == ""
    assert cell.render() == "#3 of 10 · BUY"


def test_unknown_counts_render_nothing():
    """A hand-built cell, or one from a result predating this field, is unchanged."""
    assert _cell().factor_note == ""
    assert _cell().render() == "#3 of 10 · BUY"


def test_the_marker_counts_IMPUTED_factors_as_unmeasured():
    """The end-to-end property: an abstained factor under a neutral policy is imputed from
    the others, and that is exactly what the marker exists to disclose."""
    from datetime import date

    from aristos_council.pipeline import combine_rank_results, run_rank_pipeline
    from tests.test_multi_strategy_run import RAW, STRAT_DIR, TODAY, UNIVERSE, _Adapter

    res = run_rank_pipeline(UNIVERSE, RAW, strategies_dir=STRAT_DIR, ranker_only=True,
                            adapter=_Adapter(), today=TODAY)
    rows = combine_rank_results({RAW: res}, [RAW])
    for row in rows:
        cell = row.cells[RAW]
        if cell.status != "ranked":
            continue
        ranked = next(r for r in res.ranked if r.ticker == row.ticker)
        assert cell.factors_total == len(ranked.factor_ranks)
        assert cell.factors_measured == (len(ranked.factor_ranks)
                                         - len(ranked.imputed_factors))
        # and the marker appears exactly when something was imputed
        assert bool(cell.factor_note) == bool(ranked.imputed_factors)


def test_an_excluded_cell_is_unaffected():
    cell = _cell(status="excluded", reason_plain="dividends took 120% of free cash flow",
                 factors_total=3, factors_measured=1)
    assert "ranked on" not in cell.render()
