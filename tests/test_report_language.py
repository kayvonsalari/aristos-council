"""REPORT-1 — every report readable in plain English, and the rules stated up front.

The reports were written for the person who built the system: raw code identifiers as
column headers, machine ids as the only header content, ratios as raw decimals
("observed 0.009547 vs threshold 0.015"), internal jargon as section titles, and no
summary anywhere — you had to count the ranked table by hand to learn a run produced 2
BUY and excluded 6 of 16. Worse, the header named only the SCREEN'S ID: that screen holds
six rules and the report could only ever name the ones something failed, so a reader
could not tell what had been applied at all.

This is a PRESENTATION change, so the guard that matters most is section 1: for a fixture
cohort, every NUMBER in the new output is the same number the run computed. Everything
else pins the wording rules:

- a machine id is never removed and never the only thing shown;
- every value is formatted from its DECLARED unit, never guessed at the render site;
- every rule that ran is listed with its limit and its tallies, including rules nothing
  failed;
- honest abstention keeps its reason, and a rule that could not be TESTED says so in
  words rather than hiding behind a dagger;
- and the same shared functions produce the CLI, the markdown and the HTML, so no
  surface can drift.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.factors import FACTOR_REGISTRY
from aristos_council.pipeline import (
    PROVENANCE_SECTION_TITLE,
    RULES_SECTION_TITLE,
    exclusion_rows,
    format_cli_report,
    header_lines,
    provenance_sentences,
    rules_applied,
    run_rank_pipeline,
    summary_line,
    untested_rule_notes,
    valuation_band_table,
)
from aristos_council.rank_engine import (
    RankedTicker,
    factor_column_label,
    format_score,
    ranked_table_rows,
)
from aristos_council.report_language import (
    UNITS,
    format_limit_clause,
    format_score_gloss,
    format_signed_change,
    format_summary_line,
    format_threshold,
    format_value,
    label_with_id,
    verdict_counts,
)
from aristos_council.tools.criteria.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
TODAY = date(2026, 6, 30)


# --------------------------------------------------------------------------- #
# A fixture cohort with a REAL screen: one name passes everything, one fails the
# yield floor, one fails the momentum floor, and one passes while a rule cannot be
# tested at all.
# --------------------------------------------------------------------------- #
def _bars(closes: list[float], end: date = TODAY) -> list[PriceBar]:
    from datetime import timedelta
    n = len(closes)
    return [PriceBar(day=end - timedelta(days=(n - 1 - i)), open=c, high=c, low=c,
                     close=c, adj_close=c, volume=1)
            for i, c in enumerate(closes)]


def _trend(start: float, end_value: float, n: int = 300) -> list[float]:
    step = (end_value - start) / (n - 1)
    return [start + step * i for i in range(n)]


_NAMES = {
    # GOOD: 3% yield, dividends 50% of FCF, big, price up, long streak, low debt.
    "GOOD": dict(yield_=0.03, fcf_payout=0.50, trend=(80.0, 100.0)),
    # THIN: yield below the 1.5% floor -> excluded on min_dividend_yield.
    "THIN": dict(yield_=0.009547, fcf_payout=0.50, trend=(80.0, 100.0)),
    # BROKEN: price down 14% over 12m -> excluded on min_price_momentum.
    "BROKEN": dict(yield_=0.03, fcf_payout=0.50, trend=(100.0, 86.04)),
    # UNTESTABLE: negative free cash flow -> the FCF payout rule cannot be tested.
    "UNTESTABLE": dict(yield_=0.03, fcf_payout=None, trend=(80.0, 100.0)),
}


class _Adapter(MarketDataAdapter):
    """Four rateable names built to exercise pass / fail / cannot-be-tested."""

    name = "fake-report"

    def get_fundamentals(self, ticker):
        spec = _NAMES[ticker]
        price = spec["trend"][1]
        dps = round(spec["yield_"] * price, 4)
        fcf = 1.0e9 if spec["fcf_payout"] is not None else -1.0e9
        paid = -(fcf * spec["fcf_payout"]) if spec["fcf_payout"] is not None else -1.0e8
        return Fundamentals(
            ticker=ticker, name=ticker, company_name=f"{ticker} Corporation",
            market_cap=2.0e10, currency="USD", quote_type="EQUITY",
            sector="Consumer Defensive", dividend_per_share=dps, eps=5.0,
            dividend_yield=spec["yield_"], dividend_streak_years=25,
            total_debt=5.0e9, total_cash=1.0e9,
            free_cash_flow=fcf, dividends_paid=paid,
            free_cash_flow_annual=[fcf] * 4,
            operating_income=[3.0e9] * 4, ebit=[3.0e9] * 4,
            tax_provision=[6.0e8] * 4, pretax_income=[2.9e9] * 4,
            invested_capital=[1.0e10] * 4, total_revenue=[1.0e10] * 4,
            net_income=[2.0e9] * 4)

    def get_price_history(self, ticker, *, start, end):
        lo, hi = _NAMES[ticker]["trend"]
        return PriceHistory(ticker=ticker, bars=_bars(_trend(lo, hi), end=TODAY))

    def get_dividend_history(self, ticker, *, start, end):
        return []


UNIVERSE = ["GOOD", "THIN", "BROKEN", "UNTESTABLE"]


def _run(**kw):
    return run_rank_pipeline(
        UNIVERSE, "conservative_plus_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_Adapter(), today=TODAY, **kw)


@pytest.fixture(scope="module")
def result():
    return _run()


# --------------------------------------------------------------------------- #
# 1. VALUE PARITY — the core guard: a reformat must not move a number
# --------------------------------------------------------------------------- #
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def test_every_number_in_the_report_is_a_number_the_run_computed(result):
    """The report may say anything it likes ABOUT a value; it may not say a DIFFERENT
    value. Every rendered rule limit, observed value, rank, score and factor value is
    checked back against the result's own data."""
    block = rules_applied(result)

    # thresholds: each rendered limit carries the strategy's own threshold
    for rule, sel in zip(block.rules, result.screen_strategy.criteria):
        assert rule.criterion == sel.name
        if rule.threshold_phrase != "limit not recorded":
            rendered = set(_NUMBER.findall(rule.threshold_phrase))
            raw = abs(float(sel.threshold))
            # matched at the magnitude the unit renders at: a decimal fraction shown
            # as a percent, a money amount compacted to m / bn / tn.
            candidates = [raw, raw * 100, raw / 1e6, raw / 1e9, raw / 1e12]
            assert any(_close(n, c) for n in rendered for c in candidates), \
                (rule.criterion, rule.threshold_phrase, sel.threshold)

    # tallies: they sum to the number of names actually screened
    screened = len(result.screen_outcomes)
    for rule in block.rules:
        assert rule.passed + rule.failed + rule.not_tested == screened

    # ranks and scores: the table restates the ranked rows, it does not recompute them
    rows, factor_ids = ranked_table_rows(result.ranked, result.names)
    for row, r in zip(rows, result.ranked):
        assert f"score {format_score(r.combined_rank)}" in row["Position (score)"]
        for fid in factor_ids:
            cell = row[factor_column_label(fid)]
            assert cell.split(" ")[0].rstrip("*") == f"{r.factor_ranks[fid]:.0f}"

    # exclusion sentences: each carries the observed value the screen recorded
    for row in exclusion_rows(result):
        if not row["criterion"]:
            continue
        observed = result.screen_outcomes[row["ticker"]][row["criterion"]]["observed"]
        rendered = set(_NUMBER.findall(row["sentence"]))
        assert any(_close(n, abs(observed) * 100) or _close(n, abs(observed))
                   for n in rendered), (row["ticker"], row["sentence"], observed)


def _close(rendered: str, raw: float) -> bool:
    """Does a rendered number match a raw one to the precision it was rendered at?"""
    try:
        shown = float(rendered.replace(",", ""))
    except ValueError:
        return False
    decimals = len(rendered.split(".")[1]) if "." in rendered else 0
    # magnitudes only: the SIGN is carried by the wording ("a 10% fall"), not the digits.
    return abs(abs(shown) - round(abs(raw), decimals)) < 10 ** -decimals


def test_the_reformat_moved_no_verdict_rank_or_exclusion(result):
    """The whole point: same names ranked, same verdicts, same names excluded."""
    assert [r.ticker for r in result.ranked] == ["GOOD", "UNTESTABLE"]
    assert [r.verdict for r in result.ranked] == ["buy", "hold"]
    assert {t for t, _ in result.excluded} == {"THIN", "BROKEN"}


# --------------------------------------------------------------------------- #
# 2. UNITS — one test per declared unit, formatted from the declaration
# --------------------------------------------------------------------------- #
def test_percent_scales_the_decimal_and_picks_precision_by_magnitude():
    assert format_value(0.009547, "percent") == "0.95%"      # below 1%: two decimals
    assert format_value(0.015, "percent") == "1.5%"
    assert format_value(-0.1396, "percent") == "-14.0%"      # a NEGATIVE percent
    assert format_value(1.198, "percent") == "120%"          # ABOVE 100%
    assert format_value(0.80, "percent") == "80%"            # exact -> no decimal
    assert format_value(-0.10, "percent") == "-10%"


def test_ratio_multiple_currency_count_and_score_each_read_as_themselves():
    assert format_value(2.0, "ratio") == "2.00"
    assert format_value(21.9, "multiple") == "21.9x"
    assert format_value(1.0, "multiple") == "1.0x"
    assert format_value(5_000_000_000, "currency", currency="USD") == "$5.0bn"
    assert format_value(1.2e12, "currency", currency="EUR") == "€1.2tn"
    assert format_value(7.5e6, "currency", currency="USD") == "$7.5m"
    assert format_value(10, "count") == "10"
    assert format_value(1234, "count") == "1,234"
    assert format_value(5, "score") == "5"
    assert format_value(80.0, "score") == "80"


def test_an_absent_value_says_so_and_is_never_rendered_as_zero():
    for unit in UNITS:
        assert format_value(None, unit) == "not available"


def test_a_threshold_reads_as_the_rule_it_expresses():
    assert format_threshold("min", 0.015, "percent") == "at least 1.5%"
    assert format_threshold("max", 0.80, "percent") == "at most 80%"
    # a MIN floor on a negative percent is a drawdown limit, not a target
    assert format_threshold("min", -0.10, "percent") == "no worse than -10%"
    assert format_threshold("min", 5e9, "currency", currency="USD") == "at least $5.0bn"


def test_a_limit_clause_reads_as_the_second_half_of_a_sentence():
    assert format_limit_clause("max", 0.80, "percent") == "the rule allows at most 80%"
    assert format_limit_clause("min", 0.015, "percent") == \
        "the rule requires at least 1.5%"
    # ...and a negative floor becomes a maximum permitted FALL, not a double negative
    assert format_limit_clause("min", -0.10, "percent") == \
        "the rule allows at most a 10% fall"


def test_a_change_reads_as_a_direction_and_a_magnitude():
    assert format_signed_change(-0.1396) == "fell 14.0%"
    assert format_signed_change(0.33) == "rose 33%"
    assert format_signed_change(0.0) == "was unchanged"


# --------------------------------------------------------------------------- #
# 3. THE REGISTRIES — a label and a unit are not optional
# --------------------------------------------------------------------------- #
def test_every_registered_factor_declares_a_label_and_a_valid_unit():
    """This is the test that keeps the NEXT addition honest: a factor added without a
    unit renders as a bare number somewhere, and nobody notices until a report is wrong."""
    for name, fdef in FACTOR_REGISTRY.items():
        assert fdef.label.strip(), f"{name} has no label"
        assert fdef.unit in UNITS, f"{name} declares unit {fdef.unit!r}"


def test_every_registered_criterion_declares_a_label_a_comparison_and_a_unit():
    for name, crit in REGISTRY.items():
        assert crit.label.strip(), f"{name} has no label"
        assert crit.comparison in ("min", "max"), f"{name}: {crit.comparison!r}"
        spec = crit.threshold_param
        assert spec is not None, f"{name} has no threshold param"
        assert spec.unit in UNITS, f"{name} declares unit {spec.unit!r}"


def test_a_criterion_label_is_direction_free_so_the_limit_is_not_said_twice():
    """"Minimum dividend yield — at least 1.5%" says "minimum" twice. The direction
    belongs to the threshold phrase, which is built from ``comparison``."""
    for name, crit in REGISTRY.items():
        first = crit.label.split()[0].lower()
        assert first not in ("minimum", "maximum", "min", "max"), name


# --------------------------------------------------------------------------- #
# 4. IDS — never removed, never alone
# --------------------------------------------------------------------------- #
def test_an_id_is_kept_beside_its_label_and_never_replaced_by_it():
    assert label_with_id("Defensive Income", "conservative_plus_v1") == \
        "Defensive Income (conservative_plus_v1)"
    assert label_with_id("", "conservative_plus_v1") == "conservative_plus_v1"
    assert label_with_id("Defensive Income", "") == "Defensive Income"
    assert label_with_id("same", "same") == "same"        # never "same (same)"


def test_the_header_leads_with_names_and_still_carries_every_id(result):
    lines = header_lines(result)
    text = "\n".join(lines)
    assert "Defensive Income" in text                     # the strategy's human name
    assert "conservative_plus_v1" in text                 # ...and its id
    assert "ranker only, no AI commentary" in text        # the mode, in words


def test_the_cli_report_keeps_every_id_it_used_to_print(result):
    text = format_cli_report(result)
    for machine_id in ("conservative_plus_v1", "conservative_screen_v1",
                       "min_dividend_yield", "min_price_momentum",
                       "low_volatility", "net_payout_yield", "momentum_12m"):
        assert machine_id in text, machine_id


# --------------------------------------------------------------------------- #
# 5. THE SUMMARY LINE
# --------------------------------------------------------------------------- #
def _verdicts(*verdicts) -> list[RankedTicker]:
    return [RankedTicker(ticker=f"T{i}", factor_ranks={}, factor_values={},
                         combined_rank=1.0, universe_size=len(verdicts), verdict=v)
            for i, v in enumerate(verdicts)]


def test_the_summary_line_counts_the_actual_verdicts():
    ranked = _verdicts("buy", "buy", *["hold"] * 6, "sell", "sell")
    assert verdict_counts(ranked) == {"buy": 2, "hold": 6, "sell": 2}
    assert format_summary_line(ranked, universe_size=16, excluded=6) == (
        "2 BUY · 6 HOLD · 2 SELL — 10 of 16 names ranked, 6 excluded by the screen")


def test_a_zero_category_is_omitted_rather_than_printed_as_zero():
    line = format_summary_line(_verdicts("buy", "hold", "hold"),
                               universe_size=3, excluded=0)
    assert line == "1 BUY · 2 HOLD — 3 of 3 names ranked"
    assert "SELL" not in line and "0 " not in line


def test_the_non_verdict_axes_keep_their_own_distinct_wording():
    line = format_summary_line(_verdicts("buy"), universe_size=4, excluded=1,
                               unrateable=1, fetch_errors=1)
    assert "1 excluded by the screen" in line
    assert "1 with no usable data" in line
    assert "1 whose data fetch failed" in line


def test_the_summary_line_matches_the_fixture_run(result):
    assert summary_line(result) == (
        "1 BUY · 1 HOLD — 2 of 4 names ranked, 2 excluded by the screen")


# --------------------------------------------------------------------------- #
# 6. RULES APPLIED — every rule, including the ones nothing failed
# --------------------------------------------------------------------------- #
def test_the_rules_block_lists_every_rule_in_the_screen_that_ran(result):
    block = rules_applied(result)
    assert [r.criterion for r in block.rules] == \
        [c.name for c in result.screen_strategy.criteria]
    assert len(block.rules) == 6                          # conservative_screen_v1's six


def test_a_rule_nothing_failed_still_gets_a_line(result):
    """Silence about a rule is the problem being fixed: a reader could not tell whether
    a rule had passed everything or had never been applied."""
    block = rules_applied(result)
    size = next(r for r in block.rules if r.criterion == "min_market_cap")
    assert size.failed == 0 and size.passed == len(result.screen_outcomes)
    assert size.tally == f"passed {size.passed}"


def test_each_rule_states_its_limit_in_plain_english(result):
    limits = {r.criterion: r.threshold_phrase for r in rules_applied(result).rules}
    assert limits["min_dividend_yield"] == "at least 1.5%"
    assert limits["max_payout_ratio_fcf"] == "at most 80%"
    assert limits["min_market_cap"] == "at least $5.0bn"
    assert limits["min_price_momentum"] == "no worse than -10%"
    assert limits["min_dividend_streak"] == "at least 10"
    assert limits["max_debt_to_market_cap"] == "at most 1.0x"


def test_the_tallies_separate_passed_failed_and_could_not_be_tested(result):
    rules = {r.criterion: r for r in rules_applied(result).rules}
    assert rules["min_dividend_yield"].failed == 1        # THIN
    assert rules["min_price_momentum"].failed == 1        # BROKEN
    # UNTESTABLE's free cash flow is negative -> the rule cannot be evaluated at all,
    # which is NOT a failure (house rule 3: null != false).
    assert rules["max_payout_ratio_fcf"].not_tested == 1
    assert rules["max_payout_ratio_fcf"].failed == 0


def test_the_block_says_whether_the_screen_ran_as_a_prefilter(result):
    block = rules_applied(result)
    assert block.prefilter is True
    assert "prefilter" in block.screen_note
    assert "never ranked" in block.screen_note


def test_a_non_prefilter_strategy_says_the_failing_names_were_still_ranked():
    """magic_formula_v1 declares a lens but does NOT prefilter on it — a reader must not
    be told names were filtered out when they were not."""
    res = run_rank_pipeline(["GOOD", "THIN"], "magic_formula_raw_v1", ranker_only=True,
                            strategies_dir=STRAT_DIR, adapter=_Adapter(), today=TODAY)
    block = rules_applied(res)
    assert block.prefilter is False
    assert "still ranked" in block.screen_note or "screens nothing" in block.screen_note


def test_the_block_also_states_the_rankers_own_filters(result):
    lines = " ".join(rules_applied(result).ranker_lines)
    assert "top 20% BUY, bottom 20% SELL, middle HOLD" in lines
    assert "Names ranked on:" in lines
    assert "Missing factor values are ranked worst." in lines
    assert "at least $1.0bn" in lines                     # the ranker's own cap floor


def test_the_block_is_read_from_the_strategy_that_ran_not_hardcoded(tmp_path):
    """Edit a strategy YAML — add a rule, move a threshold — and the block changes with
    no code change. This is what makes it a statement about the run rather than a
    caption someone has to remember to update."""
    import shutil
    strategies = tmp_path / "strategies"
    shutil.copytree(STRAT_DIR, strategies)
    screen = strategies / "conservative_screen_v1.yaml"
    text = screen.read_text(encoding="utf-8")
    text = text.replace("    threshold: 0.015", "    threshold: 0.02", 1)      # moved
    # a rule ADDED — inserted INTO the criteria list, not appended past its end
    text = text.replace("  - name: min_market_cap",
                        "  - name: min_roic\n    threshold: 0.05\n"
                        "  - name: min_market_cap", 1)
    screen.write_text(text, encoding="utf-8")

    block = rules_applied(run_rank_pipeline(
        UNIVERSE, "conservative_plus_v1", ranker_only=True, strategies_dir=strategies,
        adapter=_Adapter(), today=TODAY))
    limits = {r.criterion: r.threshold_phrase for r in block.rules}
    assert limits["min_dividend_yield"] == "at least 2%"          # the edited threshold
    assert "min_roic" in limits                                   # the added rule
    assert limits["min_roic"] == "at least 5%"


def test_the_measurement_basis_rides_with_the_rule_it_qualifies(result):
    """The separate "Screen basis" section is gone; the disclosure it carried is a
    column beside the rule, so the same fact is not printed twice — and is not lost."""
    rules = {r.criterion: r for r in rules_applied(result).rules}
    assert "free cash flow" in rules["max_payout_ratio_fcf"].measured


# --------------------------------------------------------------------------- #
# 7. EXCLUSIONS — a sentence, in the right units, with the id kept
# --------------------------------------------------------------------------- #
def test_an_excluded_name_names_the_rule_the_value_and_the_limit(result):
    rows = {r["ticker"]: r for r in exclusion_rows(result)}
    assert rows["THIN"]["sentence"].startswith("dividend yield 0.95%")
    assert "the rule requires at least 1.5%" in rows["THIN"]["sentence"]
    assert rows["THIN"]["criterion"] == "min_dividend_yield"      # the id, kept


def test_a_price_breakdown_reads_as_a_fall_not_a_negative_decimal(result):
    row = next(r for r in exclusion_rows(result) if r["ticker"] == "BROKEN")
    assert re.search(r"share price fell 1\d\.\d% over 12 months", row["sentence"])
    assert "the rule allows at most a 10% fall" in row["sentence"]
    assert "-0.1" not in row["sentence"]                  # no raw decimal survives


def test_a_warning_flag_becomes_its_own_line_not_brackets_mid_sentence():
    class _Result:
        ranked: list = []
        names: dict = {}
        screen_outcomes: dict = {"X": {"min_dividend_yield": {
            "passed": False, "observed": 0.005, "threshold": 0.015, "note": "",
            "basis": "", "borderline": False}}}
        excluded = [("X", "screen: min_dividend_yield (observed 0.005 vs threshold "
                          "0.015) [⚠ price diverging: +33% 12m — cyclical inflection "
                          "or mania; human review]")]
    row = exclusion_rows(_Result())[0]
    assert row["flag"].startswith("[⚠ price diverging")
    assert "⚠" not in row["sentence"]                     # lifted OUT of the sentence
    assert row["sentence"].endswith("the rule requires at least 1.5%.")


def test_an_exclusion_that_is_not_a_screen_rule_keeps_its_own_prose():
    """A cap / sector / asset-kind gate already reads as prose and has no criterion to
    look up — it is passed through untouched, never forced into a rule sentence."""
    class _Result:
        ranked: list = []
        names: dict = {}
        screen_outcomes: dict = {}
        excluded = [("QQQ", "asset kind 'ETF' outside this strategy's scope")]
    row = exclusion_rows(_Result())[0]
    assert row["sentence"] == "asset kind 'ETF' outside this strategy's scope"
    assert row["criterion"] == ""


# --------------------------------------------------------------------------- #
# 8. THE UNTESTED RULE — in words, not a bare dagger
# --------------------------------------------------------------------------- #
def test_a_rule_that_could_not_be_tested_is_stated_in_words(result):
    notes = {n["ticker"]: n for n in untested_rule_notes(result)}
    assert "UNTESTABLE" in notes
    note = notes["UNTESTABLE"]
    assert note["verdict"] in ("BUY", "HOLD", "SELL")
    joined = " ".join(note["rules"])
    assert "Dividends vs free cash flow" in joined       # the rule, by its human name
    assert "free cash flow" in joined                     # ...and the reason, kept
    assert "max_payout_ratio_fcf" in joined               # ...and the id, kept


def test_the_untested_rule_appears_in_the_report_body_not_only_as_a_symbol(result):
    text = format_cli_report(result)
    assert "RULES THAT COULD NOT BE TESTED" in text
    assert "Dividends vs free cash flow" in text


# --------------------------------------------------------------------------- #
# 9. THE RANKED TABLE
# --------------------------------------------------------------------------- #
def test_factor_columns_are_labelled_and_say_the_cells_are_ranks():
    label = factor_column_label("low_volatility")
    assert label == "Annualized volatility (low best) (rank, 1 = best)"
    assert factor_column_label("not_a_factor") == "not_a_factor (rank, 1 = best)"


def test_each_factor_cell_shows_the_value_beside_the_rank(result):
    rows, factor_ids = ranked_table_rows(result.ranked, result.names)
    cell = rows[0][factor_column_label("low_volatility")]
    rank, sep, value = cell.partition(" · ")
    assert rank.isdigit() and sep == " · "                # the RANK, then its value
    assert value.endswith("%")                            # formatted by the unit
    assert factor_ids == ["low_volatility", "net_payout_yield", "momentum_12m"]


def test_an_imputed_rank_SAYS_imputed_and_shows_no_value():
    """FACTOR-MARK-3: the word, not a footnote symbol.

    This cell sits beside a marker that says "ranked on 2 of 3 factors". "2*" is a plain
    number to a reader who has not found the legend, so the two readings looked like they
    disagreed. There is still no value to print — an imputed rank exists precisely
    because the factor had none.
    """
    r = RankedTicker(ticker="X", factor_ranks={"net_payout_yield": 2.0},
                     factor_values={"net_payout_yield": None}, combined_rank=2.0,
                     universe_size=1, imputed_factors=["net_payout_yield"])
    row = ranked_table_rows([r])[0][0]
    assert row[factor_column_label("net_payout_yield")] == "2 · imputed"


def test_the_score_gloss_is_stated_once_and_carries_both_bounds():
    assert format_score_gloss(3, 10) == (
        "Score is the sum of a name's factor ranks — lower is better. With 3 factors "
        "over 10 ranked names the best possible score is 3 and the worst is 30.")


# --------------------------------------------------------------------------- #
# 10. "Where the numbers came from" — sentences, same counts
# --------------------------------------------------------------------------- #
def test_the_provenance_section_reads_as_sentences(result):
    sentences = {e["factor"]: e["sentence"] for e in provenance_sentences(result)}
    assert sentences["low_volatility"] == \
        "Annualized volatility (low best) — real data for all 2 names."
    assert sentences["net_payout_yield"] == \
        "Net payout yield — taken from dividend yield for all 2 names."
    assert PROVENANCE_SECTION_TITLE == "Where the numbers came from"


# --------------------------------------------------------------------------- #
# 11. SURFACE PARITY — one source, no drift
# --------------------------------------------------------------------------- #
def _markdown(result) -> str:
    pytest.importorskip("streamlit")
    import app
    return app._universe_markdown(result)


def test_the_three_surfaces_carry_the_same_values(result):
    import html as _html

    from aristos_council.export.report_html import universe_report_html

    cli = format_cli_report(result)
    md = _markdown(result)
    doc = universe_report_html(result)

    # the summary line
    for surface in (cli, md):
        assert summary_line(result) in surface
    assert _html.escape(summary_line(result), quote=False) in doc

    # every rule's label, limit and tally
    for rule in rules_applied(result).rules:
        for value in (rule.label, rule.threshold_phrase, rule.tally, rule.criterion):
            assert value in cli, (value, "cli")
            assert value in md, (value, "md")
            assert _html.escape(value, quote=False) in doc, (value, "html")

    # every exclusion, on every surface.
    #
    # DETAIL-1 changed the SHAPE of this on the two report surfaces: the CLI still prints
    # one sentence per name, while the HTML and the markdown group the names under the
    # rule that removed them, state the rule once, and give each name its measured value
    # in a column. So "the same sentence appears three times" is no longer the right
    # question — but the guarantee underneath it is unchanged and is asserted here
    # directly: every excluded NAME, the VALUE it was measured at, and the RULE it missed
    # reach all three surfaces. No surface may drop a name or invent one.
    from aristos_council.pipeline import lens_detail

    detail = lens_detail(result)
    seen = set()
    for group in detail.groups:
        for name in group.names:
            seen.add(name.ticker)
            for value in (name.name, name.measured):
                if not value:
                    continue
                assert value in cli, (value, "cli")
                assert value in md, (value, "md")
                assert _html.escape(value, quote=False) in doc, (value, "html")
        if group.rule:
            assert group.rule in md and _html.escape(group.rule, quote=False) in doc
    # ...and every excluded name reached a group: nothing is silently unplaced.
    assert seen == {t for t, _ in result.excluded}

    # The per-name SENTENCE itself is still built, unchanged, and still carried by the
    # CLI and by Company Check — grouping decided where it is printed, not whether it
    # exists.
    for row in exclusion_rows(result):
        assert row["sentence"].rstrip() in cli

    # every price / valuation cell
    table = valuation_band_table(result)
    for row in table.rows:
        for column in table.columns:
            cell = row[column]
            if cell in ("—", ""):
                continue
            assert cell in cli and cell in md
            assert _html.escape(cell, quote=False) in doc


def test_the_rules_block_titles_agree_across_surfaces(result):
    from aristos_council.export.report_html import universe_report_html
    assert RULES_SECTION_TITLE == "Rules applied"
    assert RULES_SECTION_TITLE.upper() in format_cli_report(result)
    assert f"## {RULES_SECTION_TITLE}" in _markdown(result)
    assert f"<h2>{RULES_SECTION_TITLE}</h2>" in universe_report_html(result)
