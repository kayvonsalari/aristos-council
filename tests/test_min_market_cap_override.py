"""FLOOR-1 — the company-size floor as an ephemeral per-run override.

The floor is applied by the ranker BEFORE the screen and before any factor, so a name
below it is excluded before anything is measured about it. On 2026-09-15 the $5bn floor
carried by magic_formula_raw_v1 / magic_formula_momentum_v1 / growth_garp_v2 took 39 of
121 USD names, 29 of 52 services names and 27 of 41 tier-2 names out of their cohorts
before a single factor was computed — and two cohorts built specifically for mid-caps
were never graded at all.

Editing the YAML is the wrong answer (published strategy files are immutable, rule 7, and
a floor experiment is not a new strategy). The right one is the path that already exists
for disposition settings: an EPHEMERAL override applied to a COPY for one run, diffed
against the file and recorded, with the file untouched.

Two properties matter and are pinned here:

  * an override lets a name the floor excluded through, and the run RECORDS both floors;
  * NO override changes nothing at all — meta and report byte-identical — because the
    record is DIFFED, not written on intent.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.pipeline import (
    floor_override_line,
    format_floor,
    min_market_cap_override_record,
    run_multi_strategy_pipeline,
    run_rank_pipeline,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
RAW = "magic_formula_raw_v1"              # carries min_market_cap: 5.0e9
CONSERVATIVE = "conservative_plus_v1"     # carries min_market_cap: 1.0e9
TODAY = date(2026, 6, 30)

# BIG clears the $5bn floor; MID (2bn) is the name the floor excludes — the mid-cap that
# was never graded. Identical in every other respect, so the ONLY thing that can separate
# them is the floor.
_SHAPE = dict(sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400],
              tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300],
              invested_capital=[5000.0] * 4, total_revenue=[200.0, 170, 150, 120])
_FUND = {"BIG": dict(market_cap=2e10, **_SHAPE),
         "MID": dict(market_cap=2e9, **_SHAPE)}
UNIVERSE = ["BIG", "MID"]


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _run(**kw):
    return run_rank_pipeline(UNIVERSE, RAW, strategies_dir=STRAT_DIR,
                             ranker_only=True, adapter=_Adapter(), today=TODAY, **kw)


# --------------------------------------------------------------------------- #
# The override applies
# --------------------------------------------------------------------------- #
def test_without_an_override_the_mid_cap_is_excluded_before_anything_is_measured():
    res = _run()
    assert [r.ticker for r in res.ranked if not r.excluded] == ["BIG"]
    reasons = dict(res.excluded)
    assert "MID" in reasons and "below min market cap" in reasons["MID"]


def test_an_override_lets_the_mid_cap_be_ranked():
    res = _run(min_market_cap_override=1.0e9)
    assert sorted(r.ticker for r in res.ranked if not r.excluded) == ["BIG", "MID"]
    assert not any(t == "MID" for t, _ in res.excluded)


def test_the_run_records_both_the_file_floor_and_the_run_floor():
    res = _run(min_market_cap_override=1.0e9)
    assert res.meta["overrides"]["min_market_cap"] == {"file": 5.0e9, "run": 1.0e9}


def test_the_strategy_file_on_disk_is_never_touched():
    """Rule 7: published strategy files are immutable. The override is a COPY."""
    from aristos_council.pipeline import load_rank_strategy_from_id

    before = (STRAT_DIR / f"{RAW}.yaml").read_bytes()
    _run(min_market_cap_override=1.0e9)
    assert (STRAT_DIR / f"{RAW}.yaml").read_bytes() == before
    # ...and a freshly loaded strategy still carries its own floor.
    assert load_rank_strategy_from_id(RAW, STRAT_DIR).min_market_cap == 5.0e9


def test_a_zero_override_removes_the_floor_for_this_run():
    res = _run(min_market_cap_override=0.0)
    assert sorted(r.ticker for r in res.ranked if not r.excluded) == ["BIG", "MID"]
    assert res.meta["overrides"]["min_market_cap"]["run"] == 0.0


def test_the_exclusion_reason_quotes_the_EFFECTIVE_floor():
    """A reader must be able to tell WHICH floor excluded a name on a run whose floor was
    not the file's."""
    tight = run_rank_pipeline(UNIVERSE, RAW, strategies_dir=STRAT_DIR, ranker_only=True,
                              adapter=_Adapter(), today=TODAY,
                              min_market_cap_override=5.0e10)
    reasons = dict(tight.excluded)
    assert "below min market cap ($50.0bn)" in reasons["BIG"]
    assert "below min market cap ($50.0bn)" in reasons["MID"]
    # ...and with no override it quotes the FILE's floor, not a bare phrase.
    assert "below min market cap ($5.0bn)" in dict(_run().excluded)["MID"]


# --------------------------------------------------------------------------- #
# No override changes NOTHING
# --------------------------------------------------------------------------- #
def test_no_override_leaves_meta_byte_identical():
    """The whole guarantee of the batch: a ranker-only run with no overrides is unchanged."""
    assert "overrides" not in _run().meta


def test_an_override_equal_to_the_file_value_records_nothing():
    """Diffed, not written on intent — nudging the control back to the default is a no-op."""
    same = _run(min_market_cap_override=5.0e9)
    assert "overrides" not in same.meta
    assert same.meta == _run().meta


def test_the_record_helper_diffs_rather_than_asserts():
    assert min_market_cap_override_record(5.0e9, None) is None
    assert min_market_cap_override_record(5.0e9, 5.0e9) is None
    assert min_market_cap_override_record(5.0e9, 1.0e9) == {"file": 5.0e9, "run": 1.0e9}
    assert min_market_cap_override_record(None, 1.0e9) == {"file": None, "run": 1.0e9}


# --------------------------------------------------------------------------- #
# The report says so
# --------------------------------------------------------------------------- #
def test_the_report_header_line_names_both_floors():
    res = _run(min_market_cap_override=1.0e9)
    line = floor_override_line(res.meta)
    assert line == ("Company size floor overridden for this run: $1.0bn "
                    "(strategy file: $5.0bn).")


def test_there_is_no_header_line_without_an_override():
    assert floor_override_line(_run().meta) == ""
    assert floor_override_line({}) == ""


def test_the_markdown_and_html_reports_both_carry_the_line():
    pytest.importorskip("streamlit")
    import app
    from aristos_council.export.report_html import universe_report_html

    res = _run(min_market_cap_override=1.0e9)
    expected = ("Company size floor overridden for this run: $1.0bn "
                "(strategy file: $5.0bn).")
    assert expected in app._universe_markdown(res)
    assert expected in universe_report_html(res)

    # ...and neither carries it when nothing was overridden.
    plain = _run()
    assert "Company size floor overridden" not in app._universe_markdown(plain)
    assert "Company size floor overridden" not in universe_report_html(plain)


def test_the_no_override_report_is_byte_identical_to_a_run_that_never_had_the_control():
    pytest.importorskip("streamlit")
    import app

    assert app._universe_markdown(_run()) == app._universe_markdown(
        _run(min_market_cap_override=None))


# --------------------------------------------------------------------------- #
# A multi-lens run: the floor is a COHORT statement
# --------------------------------------------------------------------------- #
def test_the_override_applies_to_every_lens_in_a_multi_lens_run():
    """One floor, asked of every lens — it is a claim about who is in the room."""
    res = run_multi_strategy_pipeline(
        UNIVERSE, [RAW, CONSERVATIVE], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, min_market_cap_override=0.5e9)
    # Every lens was ASKED to use 0.5bn: each one's effective ranker floor is the override.
    for sid in (RAW, CONSERVATIVE):
        assert res.results[sid].meta["overrides"]["min_market_cap"]["run"] == 0.5e9, sid
    # The unscreened lens now ranks the mid-cap, which is the point.
    assert "MID" in {r.ticker for r in res.results[RAW].ranked if not r.excluded}


def test_a_prefilter_screens_own_market_cap_criterion_IS_now_overridden():
    """LIMIT LIFTED (2026-09-17, FLOOR-2). This test pinned the OPPOSITE until today.

    FLOOR-1 overrode ``rank_strategy.min_market_cap`` and deliberately stopped there, so a
    ``min_market_cap`` CRITERION inside a lens's prefilter screen went on excluding at its
    own threshold. The consequence was a run that said one thing and did another: on the
    tier-2 list with a $1bn override, four screened lenses still dropped names at "market
    value $4.9bn; the rule requires at least $5.0bn" beneath a header reading $1bn.

    FLOOR-2 closes it. conservative_plus_v1 carries BOTH a ranker floor (1.0e9) and a
    screen criterion (5.0e9), so it is the lens that showed the bug and the one that proves
    the fix.
    """
    res = run_rank_pipeline(UNIVERSE, CONSERVATIVE, strategies_dir=STRAT_DIR,
                            ranker_only=True, adapter=_Adapter(), today=TODAY,
                            min_market_cap_override=0.5e9)
    assert "MID" in {r.ticker for r in res.ranked if not r.excluded}
    assert "MID" not in dict(res.excluded)

    # ...and WITHOUT the override the screen's own $5bn still excludes it, under its own
    # correctly-named reason. The rule did not go away; it follows the run's floor.
    plain = run_rank_pipeline(UNIVERSE, CONSERVATIVE, strategies_dir=STRAT_DIR,
                              ranker_only=True, adapter=_Adapter(), today=TODAY)
    assert "screen: min_market_cap" in dict(plain.excluded)["MID"]


def test_the_merged_record_names_each_lens_own_file_floor():
    """The lens YAMLs disagree (raw 5.0e9, conservative 1.0e9), so one number cannot
    stand for 'the strategy file' — each lens that really changed names its own."""
    res = run_multi_strategy_pipeline(
        UNIVERSE, [RAW, CONSERVATIVE], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, min_market_cap_override=0.5e9)
    rec = res.meta["overrides"]["min_market_cap"]
    assert rec["run"] == 0.5e9
    assert rec["file"] == {RAW: 5.0e9, CONSERVATIVE: 1.0e9}
    assert "raw" in floor_override_line(res.meta) or "strategy files" in \
        floor_override_line(res.meta)


def test_a_lens_whose_file_already_matches_records_no_override():
    """An override of 1.0e9 is a real change for the raw lens and a NO-OP for the
    conservative one, whose file already says 1.0e9."""
    res = run_multi_strategy_pipeline(
        UNIVERSE, [RAW, CONSERVATIVE], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY, min_market_cap_override=1.0e9)
    assert "overrides" not in res.results[CONSERVATIVE].meta
    assert res.results[RAW].meta["overrides"]["min_market_cap"]["file"] == 5.0e9
    assert res.meta["overrides"]["min_market_cap"]["file"] == {RAW: 5.0e9}


def test_a_multi_lens_run_with_no_override_records_nothing():
    res = run_multi_strategy_pipeline(
        UNIVERSE, [RAW, CONSERVATIVE], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=TODAY)
    assert "overrides" not in res.meta


# --------------------------------------------------------------------------- #
# The formatter and the UI control's semantics
# --------------------------------------------------------------------------- #
def test_format_floor_reads_as_money():
    assert format_floor(5.0e9) == "$5.0bn"
    assert format_floor(1.0e9) == "$1.0bn"
    assert format_floor(7.5e8) == "$750m"
    assert format_floor(0.0) == "$0"
    assert format_floor(None) == "no floor"


def test_the_sidebar_control_treats_blank_and_default_as_no_override():
    pytest.importorskip("streamlit")
    import app

    assert app.floor_override_from_input(None, file_value=5.0e9) is None
    assert app.floor_override_from_input(5.0, file_value=5.0e9) is None   # the default
    assert app.floor_override_from_input(1.0, file_value=5.0e9) == 1.0e9
    assert app.floor_override_from_input(0.0, file_value=5.0e9) == 0.0    # remove it
    assert app.floor_override_from_input(2.0, file_value=None) == 2.0e9


# --------------------------------------------------------------------------- #
# FLOOR-2 — the override reaches the SCREENS, not only the ranker
# --------------------------------------------------------------------------- #
def test_the_override_re_thresholds_the_screens_min_market_cap_criterion():
    from aristos_council.pipeline import load_screen_from_id, screen_with_floor_override

    screen = load_screen_from_id("cyclical_income_screen_v1", STRAT_DIR)
    assert [c.threshold for c in screen.criteria if c.name == "min_market_cap"] == [5.0e9]

    effective, file_value = screen_with_floor_override(screen, 1.0e9)
    assert [c.threshold for c in effective.criteria
            if c.name == "min_market_cap"] == [1.0e9]
    assert file_value == 5.0e9
    # COPIED, never mutated — rule 7, published strategy files are immutable.
    assert [c.threshold for c in screen.criteria if c.name == "min_market_cap"] == [5.0e9]


def test_no_override_returns_the_SAME_screen_object():
    """Byte-identical: not an equal copy, the same object, so nothing downstream differs."""
    from aristos_council.pipeline import load_screen_from_id, screen_with_floor_override

    screen = load_screen_from_id("cyclical_income_screen_v1", STRAT_DIR)
    same, record = screen_with_floor_override(screen, None)
    assert same is screen and record is None


def test_an_override_equal_to_the_screens_own_value_is_a_no_op():
    from aristos_council.pipeline import load_screen_from_id, screen_with_floor_override

    screen = load_screen_from_id("cyclical_income_screen_v1", STRAT_DIR)
    same, record = screen_with_floor_override(screen, 5.0e9)
    assert same is screen and record is None


def test_a_two_billion_name_passes_the_screen_under_a_one_billion_override():
    from aristos_council.tools.criteria.registry import Evidence, run_screen
    from aristos_council.pipeline import load_screen_from_id, screen_with_floor_override

    screen = load_screen_from_id("cyclical_income_screen_v1", STRAT_DIR)
    effective, _ = screen_with_floor_override(screen, 1.0e9)
    mid = Fundamentals(ticker="MID", name="MID", **_FUND["MID"])
    ev = Evidence(fundamentals=mid, dividends=[], last_close=100.0)

    only = lambda st: [c for c in st.criteria if c.name == "min_market_cap"]  # noqa: E731
    assert run_screen(only(screen), ev, ticker="MID").criteria[0].passed is False
    assert run_screen(only(effective), ev, ticker="MID").criteria[0].passed is True


def test_the_rule_table_quotes_the_effective_floor():
    """rules_applied reads the screen that ACTUALLY RAN, so overriding the strategy object
    moves the Limit column with no separate change."""
    from aristos_council.pipeline import (load_screen_from_id, rules_applied,
                                          screen_with_floor_override)

    screen = load_screen_from_id("cyclical_income_screen_v1", STRAT_DIR)
    effective, _ = screen_with_floor_override(screen, 1.0e9)

    class _Res:
        screen_strategy = effective
        rank_strategy = None
        screen_outcomes = {}
        screen_bases = {}
        meta = {}
        ranked = []

    row = next(r for r in rules_applied(_Res()).rules if r.criterion == "min_market_cap")
    assert row.threshold_phrase == "at least $1.0bn"
