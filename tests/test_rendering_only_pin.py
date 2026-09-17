"""DETAIL-1 — the branch-wide pin: this work is RENDERING, and nothing else.

Every item on the DETAIL-1 branch changes how a run is DISPLAYED — a cell's markup, an
exclusion sentence's wording, a bound's name, the shape of the per-lens detail section.
None of them may move a rank, a verdict, a shortlist or a meta value. That is easy to
promise and easy to break: the grid cells are built from the same objects the detail
section reads, and a "small" change to how a cell renders is one edit away from changing
what it says.

So the grid is pinned LITERALLY. A fabricated multi-result — no adapter, no network, no
strategy files — is rendered to grid rows and compared against strings written out in
full. If any DETAIL-1 change moves a single character of a verdict cell, this fails and
the change has to be argued for rather than noticed later in a report.

The fabrication is deliberate: a real run's numbers drift with the provider, and a pin
that has to be regenerated is not a pin.
"""

from __future__ import annotations

from aristos_council.pipeline import (MultiStrategyCell, MultiStrategyResult,
                                      MultiStrategyRow, multi_strategy_grid_rows)


def _ranked(sid, position, verdict, *, measured=0, total=0):
    return MultiStrategyCell(strategy_id=sid, status="ranked", position=position,
                             cohort_size=4, verdict=verdict,
                             factors_measured=measured, factors_total=total)


def _fabricated() -> MultiStrategyResult:
    """Four names over two lenses, covering every cell axis the grid can show."""
    a, b = "cyclical_income_v1", "forensic_v1"
    rows = [
        MultiStrategyRow(ticker="AAA", display="Alpha Energy (AAA)", rank_sum=3, graded=2,
                         cells={a: _ranked(a, 1, "buy"),
                                b: _ranked(b, 2, "buy", measured=2, total=3)}),
        MultiStrategyRow(ticker="BBB", display="Beta Oil (BBB)", rank_sum=6, graded=2,
                         cells={a: _ranked(a, 3, "hold"),
                                b: _ranked(b, 3, "sell", measured=1, total=3)}),
        MultiStrategyRow(ticker="CCC", display="Gamma Gas (CCC)", graded=1,
                         comparable=False,
                         cells={a: MultiStrategyCell(
                                    strategy_id=a, status="excluded",
                                    reason="min_dividend_yield",
                                    reason_plain="the dividend yield was 0.9%; the rule "
                                                 "asks for at least 1.5%"),
                                b: _ranked(b, 4, "hold")}),
        MultiStrategyRow(ticker="DDD", display="Delta Drilling (DDD)", graded=0,
                         comparable=False,
                         cells={a: MultiStrategyCell(strategy_id=a, status="unrateable",
                                                     reason="no price history"),
                                b: MultiStrategyCell(strategy_id=b,
                                                     status="fetch_error")}),
    ]
    return MultiStrategyResult(
        strategy_ids=[a, b],
        strategy_names={a: "Cyclical Income", b: "Forensic"},
        results={}, rows=rows, meta={"universe_size": 4})


# The pin. Written out in full, on purpose: a reader reviewing a DETAIL-1 commit can see
# the exact text of every cell without running anything.
EXPECTED_HEAD = ["Name", "Cyclical Income", "Forensic"]
EXPECTED_ROWS = [
    {"Name": "Alpha Energy (AAA)",
     "Cyclical Income": "#1 of 4 · BUY",
     "Forensic": "#2 of 4 · BUY · ranked on 2 of 3 factors"},
    {"Name": "Beta Oil (BBB)",
     "Cyclical Income": "#3 of 4 · HOLD",
     "Forensic": "#3 of 4 · SELL · ranked on 1 of 3 factors"},
    {"Name": "Gamma Gas (CCC)",
     "Cyclical Income": ("excluded — the dividend yield was 0.9%; the rule asks for at "
                         "least 1.5%"),
     "Forensic": "#4 of 4 · HOLD"},
    {"Name": "Delta Drilling (DDD)",
     "Cyclical Income": "no data",
     "Forensic": "fetch failed (rerun)"},
]


def test_the_grid_rows_are_unchanged_by_anything_on_this_branch():
    rows, head = multi_strategy_grid_rows(_fabricated())
    assert head == EXPECTED_HEAD
    assert rows == EXPECTED_ROWS


def test_the_row_order_is_unchanged():
    """The grid is never re-sorted at render time — the order is decided upstream."""
    rows, _ = multi_strategy_grid_rows(_fabricated())
    assert [r["Name"] for r in rows] == [r["Name"] for r in EXPECTED_ROWS]


def test_no_cell_loses_its_verdict_word():
    """The house rule, stated as a property rather than a string: every RANKED cell names
    its verdict in capitals, whatever decoration it carries."""
    result = _fabricated()
    rows, head = multi_strategy_grid_rows(result)
    for row, grid in zip(result.rows, rows):
        for sid, header in zip(result.strategy_ids, head[1:]):
            cell = row.cells[sid]
            if cell.status == "ranked":
                assert cell.verdict.upper() in grid[header]
