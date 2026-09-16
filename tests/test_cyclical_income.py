"""CYCLICAL-INCOME-1 — the income lens for payers whose profits move with a cycle.

Defensive Income on the 135-name oil dividend cohort ranked 3 names on 2026-09-16:
min_dividend_streak >= 10 failed 126 of 128 tested, while its other five rules were
survivable (yield 15, payout-vs-FCF 33, size 14, debt 9, momentum 2). On its HOME cohort
the same lens ranked 9 of 16 with every exclusion meaningful, so the lens is right for
its purpose and the sector needed a different income test.

The four names this lens has to separate, and they are the fixture below:

  A  raised for ten years          — passes everywhere, the easy case
  B  held flat for five years      — the case the streak rule gets wrong: a streak of 0
                                     and not one cut. Chevron (9-year streak), Kinder
                                     Morgan and Hess Midstream (8) are this shape.
  C  cut in 2024                   — BP's shape (halved 2020). Must fail, and the year
                                     must be named.
  D  pays nothing                  — failed by the YIELD rule, never by the cut rule:
                                     absence of a dividend is not a cut.

Deterministic throughout: a fake adapter, no network, no LLM.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.pipeline import (
    load_rank_strategy_from_id,
    load_screen_from_id,
    run_rank_pipeline,
)
from aristos_council.strategy.discovery import (
    lens_strategy_ids,
    rank_strategies,
    visible_rank_strategies,
)

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
LENS = "cyclical_income_v1"
SCREEN = "cyclical_income_screen_v1"
TODAY = date(2026, 6, 30)

CYCLICAL = load_rank_strategy_from_id(LENS, STRAT_DIR)


# --------------------------------------------------------------------------- #
# The cohort
# --------------------------------------------------------------------------- #
def _events(*pairs):
    return [DividendEvent(ex_date=date(y, 6, 15), amount=a) for y, a in pairs]


_PARTIAL = (2026, 0.40)          # always dropped as possibly incomplete

_DIVIDENDS = {
    # A: raised every year for a decade.
    "A": _events((2016, 0.6), (2017, 0.7), (2018, 0.8), (2019, 0.9), (2020, 1.0),
                 (2021, 1.1), (2022, 1.2), (2023, 1.3), (2024, 1.4), (2025, 1.5),
                 _PARTIAL),
    # B: held flat through the downturn — streak 0, cuts 0.
    "B": _events((2016, 1.0), (2017, 1.0), (2018, 1.0), (2019, 1.0), (2020, 1.0),
                 (2021, 1.0), (2022, 1.0), (2023, 1.0), (2024, 1.0), (2025, 1.0),
                 _PARTIAL),
    # C: cut in 2024, inside the five-year window.
    "C": _events((2016, 2.0), (2017, 2.0), (2018, 2.0), (2019, 2.0), (2020, 2.0),
                 (2021, 2.0), (2022, 2.0), (2023, 2.0), (2024, 1.0), (2025, 1.0),
                 _PARTIAL),
    "D": [],                                              # pays nothing
    # LEVERED: pays fine, but the debt has become the story.
    "LEVERED": _events((2020, 1.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                       (2024, 1.0), (2025, 1.0), _PARTIAL),
}

_SHAPE = dict(sector="Energy",
              free_cash_flow_annual=[4_000.0] * 4,
              operating_income=[3_000.0] * 4,
              total_debt=3_000.0, total_cash=1_000.0)

_FUND = {
    # The last close in this fixture is ~121.90, and the yield criterion derives
    # dps / last_close rather than trusting a provider yield field (the NVDA units
    # lesson), so these per-share figures are real money.
    "A": dict(market_cap=2e10, dividend_per_share=5.0,        # ~4.1%
              dividends_paid=1_000.0, **_SHAPE),
    "B": dict(market_cap=2e10, dividend_per_share=4.5,        # ~3.7%
              dividends_paid=1_400.0, **_SHAPE),
    "C": dict(market_cap=2e10, dividend_per_share=4.0,        # ~3.3%
              dividends_paid=1_200.0, **_SHAPE),
    # D pays nothing: the yield rule is what must catch it.
    "D": dict(market_cap=2e10, dividend_per_share=0.0,
              dividends_paid=0.0, **_SHAPE),
    # LEVERED: debt 2.5x market cap -> max_debt_to_market_cap fails it.
    "LEVERED": dict(market_cap=1e10, dividend_per_share=6.0,
                    dividends_paid=1_000.0, sector="Energy",
                    free_cash_flow_annual=[4_000.0] * 4,
                    operating_income=[3_000.0] * 4,
                    total_debt=2.5e10, total_cash=1_000.0),
}

UNIVERSE = ["A", "B", "C", "D", "LEVERED"]


# What the adapter derives from the same history: a 10-year raiser, a flat payer (streak
# 0 — the case this lens exists for), and a cutter. Defensive Income's min_dividend_streak
# reads THIS scalar, not the events, so without it that rule would abstain and the
# regression below could not be stated.
_STREAK_YEARS = {"A": 10, "B": 0, "C": 0, "D": None, "LEVERED": 0}


def _year_totals(ticker):
    """What a real adapter carries on Fundamentals: the per-calendar-year totals it
    already summed to derive the streak and the last-cut year (CRIT-NOCUT-1). The rank
    prefilter never sees dividend EVENTS, so this is the source the screen reads."""
    annual: dict[int, float] = {}
    for ev in _DIVIDENDS[ticker]:
        annual[ev.ex_date.year] = annual.get(ev.ex_date.year, 0.0) + ev.amount
    return [[float(y), annual[y]] for y in sorted(annual)] or None


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, quote_type="EQUITY",
                            dividend_year_totals=_year_totals(ticker),
                            dividend_streak_years=_STREAK_YEARS[ticker],
                            **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return list(_DIVIDENDS[ticker])


def _run(universe=None):
    return run_rank_pipeline(universe or UNIVERSE, LENS, strategies_dir=STRAT_DIR,
                             ranker_only=True, adapter=_Adapter(), today=TODAY)


# --------------------------------------------------------------------------- #
# It loads, and it is reachable
# --------------------------------------------------------------------------- #
def test_the_lens_loads_with_its_three_income_durability_legs():
    assert CYCLICAL.id == LENS
    assert CYCLICAL.display_name == "Cyclical Income"
    assert [f.name for f in CYCLICAL.factors] == [
        "net_payout_yield", "payout_coverage_fcf", "net_debt_to_operating_income"]
    # Two of the three are LOW-direction: a big yield out of borrowed money is the trap
    # this lens exists to avoid, not the thing it looks for.
    from aristos_council.factors import FACTOR_REGISTRY
    directions = [FACTOR_REGISTRY[f.name].direction for f in CYCLICAL.factors]
    assert directions == ["high", "low", "low"]


def test_it_is_a_visible_rank_strategy_and_its_screen_is_a_hidden_lens():
    assert LENS in {s.id for s in rank_strategies(STRAT_DIR)}
    assert LENS in {s.id for s in visible_rank_strategies(STRAT_DIR)}
    # The screen is DERIVED as a lens (referenced by a rank strategy) and stays hidden.
    assert SCREEN in lens_strategy_ids(STRAT_DIR)
    assert SCREEN not in {s.id for s in visible_rank_strategies(STRAT_DIR)}


def test_it_appears_in_the_run_tabs_strategy_options():
    pytest.importorskip("streamlit")
    import app

    ids = [s.id for _, _, s in app.list_rank_strategy_options(STRAT_DIR)]
    assert LENS in ids


def test_the_screen_carries_the_income_floors_and_neither_removed_rule():
    screen = load_screen_from_id(SCREEN, STRAT_DIR)
    names = [c.name for c in screen.criteria]
    assert names == ["min_dividend_yield", "max_payout_ratio_fcf", "min_market_cap",
                     "max_dividend_cuts", "max_debt_to_market_cap"]
    # The two rules whose removal IS this lens — absent by design, not by oversight.
    assert "min_dividend_streak" not in names
    assert "min_price_momentum" not in names
    # The floors that stayed are the defensive screen's, unchanged.
    defensive = load_screen_from_id("conservative_screen_v1", STRAT_DIR)
    shared = {c.name: c.threshold for c in defensive.criteria}
    for c in screen.criteria:
        if c.name in shared:
            assert c.threshold == shared[c.name], c.name


def test_the_screen_runs_as_a_prefilter():
    assert CYCLICAL.prefilter_screen is True
    assert CYCLICAL.council_screen_strategy == SCREEN


# --------------------------------------------------------------------------- #
# The four names it has to separate
# --------------------------------------------------------------------------- #
def test_the_raiser_and_the_flat_payer_are_both_ranked():
    """B is the whole point: a streak of 0, not one cut, and it must be ranked."""
    ranked = {r.ticker for r in _run().ranked if not r.excluded}
    assert {"A", "B"} <= ranked


def test_the_cutter_is_excluded_and_the_year_is_named():
    from aristos_council.pipeline import exclusion_rows

    res = _run()
    reasons = dict(res.excluded)
    assert "C" in reasons
    assert "max_dividend_cuts" in reasons["C"]          # the terse reason names the rule
    # The YEAR rides in the per-criterion record the report renders, not in the terse
    # reason string (which the scoreboard parses and must stay stable).
    assert "dividend cut in 2024" in res.screen_outcomes["C"]["max_dividend_cuts"]["note"]
    row = next(r for r in exclusion_rows(res) if r["criterion"] == "max_dividend_cuts")
    assert "the dividend was cut" in row["sentence"]


def test_the_non_payer_is_excluded_by_yield_not_by_the_cut_rule():
    """Absence of a dividend is not a cut — house rule 3 in a sentence."""
    reasons = dict(_run().excluded)
    assert "D" in reasons
    assert "min_dividend_yield" in reasons["D"]
    assert "max_dividend_cuts" not in reasons["D"]


def test_a_name_whose_debt_exceeds_its_market_cap_is_excluded():
    reasons = dict(_run().excluded)
    assert "LEVERED" in reasons
    assert "max_debt_to_market_cap" in reasons["LEVERED"]


def test_the_flat_payer_would_have_been_excluded_by_the_defensive_lens():
    """The regression this lens exists to fix, asserted directly: the SAME name, the
    SAME data, excluded by Defensive Income's streak rule and ranked by this one."""
    defensive = run_rank_pipeline(["A", "B"], "conservative_plus_v1",
                                  strategies_dir=STRAT_DIR, ranker_only=True,
                                  adapter=_Adapter(), today=TODAY)
    assert "B" in dict(defensive.excluded)
    assert "min_dividend_streak" in dict(defensive.excluded)["B"]
    assert "B" in {r.ticker for r in _run().ranked if not r.excluded}


# --------------------------------------------------------------------------- #
# The report says what the new rule is
# --------------------------------------------------------------------------- #
def test_the_rules_table_renders_the_new_criterion_in_plain_english():
    from aristos_council.pipeline import rules_applied

    rules = rules_applied(_run())
    row = next(r for r in rules.rules if r.criterion == "max_dividend_cuts")
    assert row.label == "Dividend cuts in the last N years"
    # Its threshold is a WINDOW, so the phrase must read as a rule, not as a cap.
    assert row.threshold_phrase == ("no year paid less than the year before, "
                                    "across the last 5 complete years")
    assert row.failed == 1 and row.passed >= 2       # C failed; A and B passed


def test_the_exclusion_sentence_reads_as_english():
    from aristos_council.pipeline import exclusion_rows

    row = next(r for r in exclusion_rows(_run())
               if r["criterion"] == "max_dividend_cuts")
    assert "the dividend was cut" in row["sentence"]
    # ...and the limit clause states the RULE, not a cap on the observed value.
    assert "no year paid less than the year before" in row["sentence"]
