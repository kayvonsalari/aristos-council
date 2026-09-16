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


# --------------------------------------------------------------------------- #
# FACTOR-MARK-2 — the marker must not cost the cell its colour
# --------------------------------------------------------------------------- #
# The marker lands AFTER the verdict word, and the grid's verdict detector matched only on
# "ends with · VERDICT". So every marked cell fell through to the plain branch: no
# cell-buy/hold/sell tint, no coloured verdict-* word. 37 cells in the Forensic column of
# the 2026-09-16 oil run (9 BUY, 17 HOLD, 11 SELL) — the disclosure cost the disclosure's
# subject its grading, which is the wrong way round.

def test_the_verdict_is_still_found_behind_the_marker():
    from aristos_council.export.report_html import verdict_of_cell

    assert verdict_of_cell("#3 of 10 · BUY · ranked on 2 of 3 factors") == "BUY"
    assert verdict_of_cell("#3 of 10 · HOLD · ranked on 1 of 4 factors") == "HOLD"
    assert verdict_of_cell("#3 of 10 · SELL · ranked on 2 of 3 factors") == "SELL"
    # and an unmarked cell is untouched
    assert verdict_of_cell("#3 of 10 · BUY") == "BUY"


def test_a_cell_on_another_axis_is_still_not_a_verdict():
    """The marker tolerance must not turn an exclusion into a colourable cell."""
    from aristos_council.export.report_html import verdict_of_cell

    assert verdict_of_cell("excluded — dividends took 120% of free cash flow") == ""
    assert verdict_of_cell("no data") == ""
    assert verdict_of_cell("fetch failed (rerun)") == ""
    assert verdict_of_cell("—") == ""


def test_a_marked_cell_renders_the_coloured_word_AND_the_marker():
    """The item's own acceptance: one cell, both things in it."""
    from aristos_council.export.report_html import _verdict_grid_cell

    html = _verdict_grid_cell(_cell(factors_total=3, factors_measured=2).render())
    assert "verdict-buy" in html                      # the coloured word
    assert ">BUY<" in html                            # ...which is still a WORD
    assert "ranked on 2 of 3 factors" in html         # the marker survives
    assert "muted" in html                            # ...muted, after the verdict
    assert html.index("verdict-buy") < html.index("ranked on 2 of 3 factors")


def test_a_marked_row_keeps_its_cell_tint_in_the_rendered_grid():
    """End to end through the real report builder: a marked cell carries its cell-* tint.

    The marker is stamped onto a real run's cell rather than fabricated around the
    renderer, so the path under test is the one a report actually takes."""
    from aristos_council.export.report_html import multi_strategy_report_html
    from tests.test_merged_multi_report import _multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    result = _multi([SCREENED, RAW])
    marked = next(row for row in result.rows
                  if row.cells[RAW].status == "ranked"
                  and row.cells[RAW].verdict.lower() in {"buy", "hold", "sell"})
    cell = marked.cells[RAW]
    cell.factors_total, cell.factors_measured = 3, 2
    assert cell.factor_note                     # the row really is marked now

    doc = multi_strategy_report_html(result)
    assert "ranked on 2 of 3 factors" in doc
    assert f'cell-{cell.verdict.lower()}' in doc
    assert f'verdict-{cell.verdict.lower()}' in doc


def test_the_markdown_export_keeps_the_marker_as_plain_text():
    """Markdown has no spans to lose, so the marker is simply the text it always was."""
    from aristos_council.pipeline import multi_strategy_grid_rows
    from tests.test_rendering_only_pin import _fabricated

    rows, head = multi_strategy_grid_rows(_fabricated())
    assert rows[0]["Forensic"] == "#2 of 4 · BUY · ranked on 2 of 3 factors"
