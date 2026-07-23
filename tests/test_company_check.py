"""Company Check — single-name diagnostic (ITEM 3).

The feature answers "why isn't X on the list?" for ONE ticker WITHOUT ever emitting a
verdict (a rank over a class of one is fabricated). Four shapes, each an actual demo
answer: a fundamental-fail-with-momentum name (MU), a sector-excluded name (GS), a
no-data name (PARA), and a passing name. Deterministic — fake adapter, no network,
no LLM, no reference run needed (raw-values fallback exercised).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.company_check import format_company_check, run_company_check
from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"

_STRAT = "magic_formula_momentum_v1"          # lens: magic_value_screen (min_roic, min_cap)


def _rising(n=260, base=100.0, step=0.002):
    closes = [base * (1 + step * i) for i in range(n)]
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                 adj_close=c, volume=10) for c in closes])


class _OneName(MarketDataAdapter):
    """Serves exactly one shaped name; anything else is a no-data shell."""

    name = "fake"

    def __init__(self, fundamentals, *, has_price=True):
        self._f = fundamentals
        self._has_price = has_price

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        if not self._has_price:
            raise RuntimeError("no timezone found, symbol may be delisted")
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _check(fundamentals, *, ticker="X", has_price=True, reference="", strat=_STRAT):
    return run_company_check(
        ticker, strat, reference, adapter=_OneName(fundamentals, has_price=has_price),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))


def _no_verdict(text: str) -> bool:
    """The output must never ISSUE a verdict. It states 'NO VERDICT' and may clarify a
    screen fail is 'NOT a SELL' — neither is a verdict assignment. Guard against a
    'Verdict: BUY/HOLD/SELL' line."""
    return ("NO VERDICT" in text
            and not any(f"Verdict: {v}" in text for v in ("BUY", "HOLD", "SELL")))


# --------------------------------------------------------------------------- #
# MU-shaped — fails min_roic, price has run up: full table + divergence flag, no verdict
# --------------------------------------------------------------------------- #
_MU = Fundamentals(
    ticker="MU", company_name="Micron Technology Incorporated", market_cap=1.2e11,
    sector="Technology", ebit=[3000.0], pe_ratio=15.0,
    operating_income=[500.0] * 4, tax_provision=[100.0] * 4,
    pretax_income=[480.0] * 4, invested_capital=[8000.0] * 4,   # ROIC ~4.9% < 12%
    total_revenue=[250.0, 200, 170, 150])


def test_mu_shaped_full_table_flag_and_no_verdict():
    r = _check(_MU, ticker="MU")
    assert not r.unrateable
    # ALL criteria evaluated (min_roic FAIL) — not short-circuited at the first fail.
    statuses = {c.name: c.status for c in r.screen}
    assert statuses["min_roic"] == "FAIL"
    # ITEM 3: market cap is deduped out of the SCREEN (same floor as the gate) and shown
    # ONCE under GATES; the flag records that.
    assert "min_market_cap" not in statuses
    assert r.market_cap_in_gates is True
    assert any(g.name == "min_market_cap" and g.status == "PASS" for g in r.gates)
    # every rank factor is reported with a raw value + context (no reference -> raw).
    assert {f.factor for f in r.factors} == {"roic", "earnings_yield", "momentum_12m"}
    assert all("no reference run available" in f.context for f in r.factors)
    # the price/fundamentals divergence flag fires (min_roic fail + momentum >= +0.30).
    assert r.divergence_flag is not None and "price diverging" in r.divergence_flag
    # the '⚠' flag glyph is present and the report is UTF-8 encodable — the CLIs force
    # UTF-8 stdout so this doesn't crash a Windows cp1252 console (found in validation).
    assert "⚠" in format_company_check(r)
    format_company_check(r).encode("utf-8")
    # NO verdict anywhere; the object has no verdict field at all.
    assert not hasattr(r, "verdict")
    text = format_company_check(r)
    assert _no_verdict(text)
    assert "min_roic" in text and "Micron Technology Incorporated (MU)" in text


# --------------------------------------------------------------------------- #
# GS-shaped — sector-excluded: the sector gate is shown as the reason
# --------------------------------------------------------------------------- #
_GS = Fundamentals(
    ticker="GS", company_name="Goldman Sachs Group", market_cap=1.5e11,
    sector="Financial Services", ebit=[15000.0], pe_ratio=13.0,
    operating_income=[15000.0] * 4, tax_provision=[3000.0] * 4,
    pretax_income=[14000.0] * 4, invested_capital=[50000.0] * 4,
    total_revenue=[500.0, 480, 460, 440])


def test_gs_shaped_sector_gate_is_the_reason():
    r = _check(_GS, ticker="GS")
    assert not r.unrateable
    sector_gate = next(g for g in r.gates if g.name == "sector")
    assert sector_gate.status == "FAIL"
    assert "Financial Services" in sector_gate.detail
    text = format_company_check(r)
    assert _no_verdict(text)
    assert "sector" in text and "EXCLUDED" in r.pointer


# --------------------------------------------------------------------------- #
# ITEM 2 — sector-exclusion rationale (config-driven, never hardcoded)
# --------------------------------------------------------------------------- #
class _Strat:
    exclude_sectors = ["Financial Services"]
    min_market_cap = None
    max_payout_ratio = None

    def __init__(self, rationale=""):
        self.sector_exclusion_rationale = rationale


def test_sector_rationale_renders_only_when_configured():
    from aristos_council.company_check import _gate_cells
    f = Fundamentals(ticker="GS", sector="Financial Services", market_cap=1e11)

    # configured -> the rationale rides on the sector gate cell
    configured = next(g for g in _gate_cells(_Strat("ROIC not computable for banks"), f)
                      if g.name == "sector")
    assert configured.status == "FAIL"
    assert configured.rationale == "ROIC not computable for banks"

    # not configured -> bare gate line, no rationale (never hardcoded)
    bare = next(g for g in _gate_cells(_Strat(""), f) if g.name == "sector")
    assert bare.status == "FAIL" and bare.rationale == ""


def test_magic_formula_rationale_flows_into_the_report_verbatim():
    r = _check(_GS, ticker="GS", strat="magic_formula_momentum_v1")
    sector_gate = next(g for g in r.gates if g.name == "sector")
    assert "not computable on a comparable basis for financials" in sector_gate.rationale
    assert "earnings_yield falls back to P/E" in sector_gate.rationale
    # and it renders into the text report after the gate line.
    assert sector_gate.rationale in format_company_check(r)


# --------------------------------------------------------------------------- #
# PARA-shaped — no data: UNRATEABLE-style honest output, no fabricated values
# --------------------------------------------------------------------------- #
def test_para_shaped_unrateable_no_fabricated_values():
    r = _check(Fundamentals(ticker="PARA"), ticker="PARA", has_price=False)
    assert r.unrateable
    assert r.screen == [] and r.gates == [] and r.factors == []   # nothing fabricated
    assert r.divergence_flag is None
    text = format_company_check(r)
    assert _no_verdict(text)
    assert "UNRATEABLE" in text


# --------------------------------------------------------------------------- #
# Passing name — all-pass table, still NO verdict, points at a universe run
# --------------------------------------------------------------------------- #
_GOOD = Fundamentals(
    ticker="GOOD", company_name="Good Quality Corp", market_cap=8e10,
    sector="Technology", quote_type="EQUITY", ebit=[4000.0], pe_ratio=18.0,
    operating_income=[2000.0] * 4, tax_provision=[400.0] * 4,
    pretax_income=[1900.0] * 4, invested_capital=[8000.0] * 4,   # ROIC ~19.7% >= 12%
    total_revenue=[300.0, 280, 260, 240])


def test_passing_name_all_pass_no_verdict_points_at_universe_run():
    r = _check(_GOOD, ticker="GOOD")
    assert not r.unrateable
    assert all(c.status == "PASS" for c in r.screen)              # all-pass table
    assert all(g.status == "PASS" for g in r.gates)
    assert r.divergence_flag is None                             # passes -> no fund. fail
    text = format_company_check(r)
    assert _no_verdict(text)
    assert "Passes the screen" in r.pointer
    assert "universe run" in r.pointer


# --------------------------------------------------------------------------- #
# ITEM 3 cosmetic sweep
# --------------------------------------------------------------------------- #
def test_momentum_factor_renders_as_signed_percent():
    from aristos_council.company_check import format_factor_value
    # matches the divergence flag's +711%, never the raw ratio 7.11.
    assert format_factor_value("momentum_12m", 7.11) == "+711%"
    assert format_factor_value("momentum_6m", -0.083) == "-8%"
    # a non-momentum factor is untouched by the percent rule.
    assert format_factor_value("roic", 0.0482) == "0.0482"
    assert format_factor_value("momentum_12m", None) == "—"
    # and it flows into the report text for MU (momentum ~+50% over the rising fixture).
    r = _check(_MU, ticker="MU")
    mom = next(f for f in r.factors if f.factor == "momentum_12m")
    assert format_factor_value("momentum_12m", mom.value).endswith("%")
    assert format_factor_value("momentum_12m", mom.value) in format_company_check(r)


# --------------------------------------------------------------------------- #
# ETFCHK-2 — plain-English gloss on the ETF expense-ratio line
# --------------------------------------------------------------------------- #
# ETFCHK-3: the vendor net_expense_ratio is a PERCENT (SCHD 0.06% -> 0.06), NOT a
# fraction. VYM's 0.06% expense ratio arrives as 0.06 (probe 2026-07-15).
_ETF = Fundamentals(
    ticker="VYM", company_name="Vanguard High Dividend Yield ETF",
    net_expense_ratio=0.06, total_assets=6.0e10, dividend_yield=0.028,
    quote_type="ETF")


def test_expense_ratio_gloss_renders_for_etf_strategy():
    r = _check(_ETF, ticker="VYM", strat="etf_dividend_v1")
    text = format_company_check(r)
    # the fee is spelled out in plain English (ETFCHK-3: the 0.06% percent value 0.06
    # is a €0.60 annual fee per €1,000, NOT €60.00).
    assert "the fund's annual fee: €0.60 per €1,000 held, charged every year" in text
    assert "€60.00" not in text                              # the old 100×-off reading
    # the gloss lives ON the expense_ratio line, and the basis tag ([source]) and cohort
    # context (— …) around it are untouched.
    line = next(l for l in text.splitlines() if "(expense_ratio):" in l)
    assert "the fund's annual fee: €0.60 per €1,000 held" in line
    assert "[" in line and "]" in line                       # basis/source tag intact
    assert "no reference run available" in line               # cohort context intact


def test_expense_ratio_per_1000_arithmetic():
    from aristos_council.company_check import _expense_ratio_gloss
    # ETFCHK-3 fixtures: the vendor value is a PERCENT, so fee = (value/100) × €1,000 ==
    # value × 10. 0.06 -> €0.60, 0.61 -> €6.10 (two decimals).
    assert _expense_ratio_gloss(0.06) == (
        " — the fund's annual fee: €0.60 per €1,000 held, charged every year")
    assert _expense_ratio_gloss(0.61) == (
        " — the fund's annual fee: €6.10 per €1,000 held, charged every year")
    # a missing ratio has nothing to gloss.
    assert _expense_ratio_gloss(None) == ""


def test_stock_strategy_output_unchanged_by_gloss():
    # a stock strategy has no expense_ratio factor, so the gloss never appears — its
    # factor block is byte-for-byte what it was before ETFCHK-2.
    text = format_company_check(_check(_MU, ticker="MU"))
    assert "annual fee" not in text
    assert "per €1,000 held" not in text


def test_market_cap_deduped_only_when_floors_match():
    # Flagship: lens-screen floor (5B) == rank gate (5B) -> deduped to GATES only.
    flag = _check(_MU, ticker="MU")
    assert flag.market_cap_in_gates is True
    assert "min_market_cap" not in {c.name for c in flag.screen}
    assert "shown once, under GATES" in format_company_check(flag)

    # conservative_plus: screen floor (5B) != rank gate (1B) -> genuinely distinct, kept
    # in BOTH (a real constraint, not a duplicate).
    cons = _check(_MU, ticker="MU", strat="conservative_plus_v1")
    assert cons.market_cap_in_gates is False
    assert "min_market_cap" in {c.name for c in cons.screen}


# --------------------------------------------------------------------------- #
# 4C ITEM 2 / CCFIX-3 — Company Check under growth_garp_v1 (GARP) renders growth
# criteria with gating labels derived from the screen RUNNER.
# --------------------------------------------------------------------------- #
class _GrowthAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(
            ticker=ticker, company_name="NVIDIA Corporation", market_cap=2e12,
            sector="Technology", total_revenue=[600, 400, 270, 170],
            operating_income=[350, 250, 150, 90], ebit=[350],
            tax_provision=[50, 40, 25, 15], pretax_income=[340, 240, 145, 88],
            invested_capital=[300] * 4, pe_ratio=45, total_debt=1e10, total_cash=3e10)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=100, low=100,
                     close=100 + 0.2 * i, adj_close=100 + 0.2 * i, volume=1)
            for i in range(260)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_company_check_growth_garp_renders_criteria_with_gating_labels():
    r = run_company_check(
        "NVDA", "growth_garp_v1", "", adapter=_GrowthAdapter(),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    assert r.rank_strategy_id == "growth_garp_v1"
    assert r.screen_strategy_id == "growth_screen_v1"                 # the GARP lens
    # the four growth criteria render (min_market_cap dedups into GATES).
    names = {c.name for c in r.screen}
    assert {"min_revenue_cagr", "max_peg_ratio", "min_roic",
            "min_price_momentum"} <= names
    for c in r.screen:                                               # observed-vs-threshold present
        assert c.status in ("PASS", "FAIL", "NOT-EVALUATED")
    # CCFIX-3: the prefilter EXCLUDES on any confirmed fail, so an EVALUATED criterion is
    # GATING. min_price_momentum here evaluates (PASS) -> gating (the runner enforces it),
    # NOT the old is_gating-flag "non-gating".
    mom = next(c for c in r.screen if c.name == "min_price_momentum")
    assert mom.status in ("PASS", "FAIL") and mom.gating is True
    text = format_company_check(r)
    assert "min_price_momentum" in text and "gating" in text
    assert _no_verdict(text)


# --------------------------------------------------------------------------- #
# CCFIX-2 — screen-less strategy: no default-lens fallback.
# --------------------------------------------------------------------------- #
def test_screen_less_strategy_has_no_criteria_and_no_screen_exclusion_claim():
    # magic_formula_raw_v1 declares no lens -> Company Check must NOT fall back to the
    # default (growth_v1) lens and diagnose against the growth screen.
    r = _check(_MU, ticker="MU", strat="magic_formula_raw_v1")
    assert r.screen_less is True
    assert r.screen == []                             # no criterion lines
    assert r.screen_strategy_id == ""                 # NOT defaulted to growth_v1
    assert any(g.name in ("sector", "min_market_cap") for g in r.gates)  # gates still render
    text = format_company_check(r)
    assert "no lens screen" in text and "screens nothing" in text
    assert "a screen fail" not in text                # no screen-exclusion claim
    # no growth/value criteria leaked from the default lens
    assert "min_revenue_cagr" not in text and "min_roic" not in text
    assert _no_verdict(text)


def test_screen_less_gate_fail_names_the_gate_not_a_screen():
    # a financial under RAW -> the sector GATE fails; the pointer names the gate, and
    # never claims a screen exclusion (RAW screens nothing).
    r = _check(_GS, ticker="GS", strat="magic_formula_raw_v1")
    assert r.screen_less is True and r.screen == []
    sector = next(g for g in r.gates if g.name == "sector")
    assert sector.status == "FAIL"
    assert "GATE fail" in r.pointer and "sector" in r.pointer
    assert "screen fail" not in r.pointer


def test_screened_strategies_are_unchanged():
    # regression: strategies WITH a lens still render criteria and a lens id.
    for strat in ("magic_formula_momentum_v1", "growth_garp_v2", "conservative_plus_v1"):
        r = _check(_MU, ticker="MU", strat=strat)
        assert r.screen_less is False
        assert r.screen_strategy_id                    # non-empty lens id
        assert len(r.screen) >= 1                       # criteria render


def test_council_path_default_resolution_is_untouched():
    # CCFIX-2 changes only Company Check's use of the default; the council path's
    # resolve_council_screen_id keeps its blunt default exactly as before.
    from aristos_council.pipeline import (
        load_rank_strategy_from_id, resolve_council_screen_id)
    raw = load_rank_strategy_from_id("magic_formula_raw_v1", STRAT_DIR)
    assert resolve_council_screen_id(raw) == "growth_v1"          # default still there
    assert resolve_council_screen_id(raw, "magic_value_screen_v1") == "magic_value_screen_v1"


# --------------------------------------------------------------------------- #
# CCFIX-3 — gating tag derives from the enforcing runner (prefilter excludes on any
# confirmed fail; never on an abstention), not the disposition is_gating flag.
# --------------------------------------------------------------------------- #
def test_growth_criteria_tag_gating_under_v2():
    # the v2 baseline excluded 31 names on exactly these criteria -> they are GATING.
    r = run_company_check(
        "NVDA", "growth_garp_v2", "", adapter=_GrowthAdapter(),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    assert r.screen_strategy_id == "growth_screen_v2"
    by = {c.name: c for c in r.screen}
    for name in ("min_revenue_cagr", "min_roic", "max_peg_ratio"):
        assert by[name].status in ("PASS", "FAIL")        # evaluated
        assert by[name].gating is True                    # the runner excludes on a fail
    assert "non-gating" not in format_company_check(r)     # no evaluated criterion is non-gating


class _AbstainRoicAdapter(MarketDataAdapter):
    """revenue present (min_revenue_cagr evaluates) but NO invested_capital -> min_roic
    ABSTAINS -> genuinely non-gating (the runner will not exclude on an abstention)."""

    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(
            ticker=ticker, market_cap=2e10, sector="Technology",
            total_revenue=[200, 170, 145, 124], operating_income=[100, 88, 78, 68],
            ebit=[100], pe_ratio=18, invested_capital=[])     # empty -> roic NOT-EVAL

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                     adj_close=c, volume=1) for c in [100 + 0.2 * i for i in range(260)]])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_abstaining_criterion_tags_non_gating():
    r = run_company_check(
        "X", "growth_garp_v2", "", adapter=_AbstainRoicAdapter(),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    roic = next(c for c in r.screen if c.name == "min_roic")
    assert roic.status == "NOT-EVALUATED"                 # abstains
    assert roic.gating is False                           # runner would NOT exclude on it
    cagr = next(c for c in r.screen if c.name == "min_revenue_cagr")
    assert cagr.status in ("PASS", "FAIL") and cagr.gating is True   # evaluated -> gating
    assert "non-gating" in format_company_check(r)        # the abstaining one is labelled


class _PegMustFailAdapter(MarketDataAdapter):
    """operating income DECLINING -> PEG growth <= 0 -> must-fail (passed False,
    observed None) — the branch that used to render a bare '— vs threshold 2'."""

    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(
            ticker=ticker, market_cap=2e10, sector="Technology",
            total_revenue=[200, 170, 145, 124], operating_income=[68, 78, 88, 100],
            ebit=[68], pe_ratio=18, invested_capital=[500] * 4,
            tax_provision=[10] * 4, pretax_income=[60] * 4)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                     adj_close=c, volume=1) for c in [100 + 0.2 * i for i in range(260)]])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_peg_must_fail_renders_its_reason_not_a_bare_threshold():
    r = run_company_check(
        "X", "growth_garp_v2", "", adapter=_PegMustFailAdapter(),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    peg = next(c for c in r.screen if c.name == "max_peg_ratio")
    assert peg.status == "FAIL" and peg.observed is None and peg.gating is True
    assert peg.note                                       # a reason is present
    text = format_company_check(r)
    assert "not growing" in text                          # the reason renders
    assert "max_peg_ratio               observed — vs threshold" not in text   # not the bare form


def test_universe_run_screen_behaviour_is_unchanged():
    # CCFIX-3 changes ONLY the Company Check tag/rendering — the screen RUNNER is
    # untouched, so a rank run still excludes on the screen exactly as before.
    from aristos_council.pipeline import run_rank_pipeline

    class _A(MarketDataAdapter):
        name = "fake"

        def get_fundamentals(self, ticker):
            return _MU if ticker == "MU" else _GOOD

        def get_price_history(self, ticker, *, start, end):
            return _rising()

        def get_dividend_history(self, ticker, *, start, end):
            return []

    r = run_rank_pipeline(["MU", "GOOD"], "magic_formula_momentum_v1", ranker_only=True,
                          strategies_dir=STRAT_DIR, adapter=_A(), today=date(2026, 6, 30))
    assert any(t == "MU" and "min_roic" in why for t, why in r.excluded)   # screen unchanged
    assert "GOOD" in {x.ticker for x in r.ranked}


# --------------------------------------------------------------------------- #
# Spec 4D — VERDICT OF RECORD: quote the checked name's outcome verbatim from the latest
# frozen run of the reference universe. Company Check still NEVER issues a verdict; it
# only REPORTS one already recorded for this name in a past universe run.
# --------------------------------------------------------------------------- #
_FIN_TICKERS = ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "SCHW", "BLK",
                "AXP", "V", "MA", "COF", "MET", "AIG"]          # financials_16_v1


class _FinAdapter(MarketDataAdapter):
    """The 16 financials of financials_16_v1, shaped for a deterministic P/B + ROE +
    momentum ranking. GS is given the WORST P/B and ROE so it ranks strictly LAST (SELL,
    rank 16 of 16). ``low_cap`` names get a sub-floor market cap so they are EXCLUDED
    (below the 5B gate) in the frozen run."""

    name = "fake"

    def __init__(self, *, low_cap=()):
        self._low_cap = set(low_cap)

    def get_fundamentals(self, ticker):
        i = _FIN_TICKERS.index(ticker) if ticker in _FIN_TICKERS else 99
        if ticker == "GS":
            pb, roe = 5.0, 0.02                     # worst on both -> ranked last
        else:
            pb, roe = 1.0 + 0.1 * i, 0.30 - 0.005 * i
        cap = 2e9 if ticker in self._low_cap else 5e10
        return Fundamentals(
            ticker=ticker, company_name=f"{ticker} Corp", market_cap=cap,
            sector="Financial Services", price_to_book=pb, return_on_equity=roe)

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _freeze_financials_run(runs_dir, *, low_cap=()):
    """Freeze one financials_v1 run over financials_16_v1 into ``runs_dir`` and return it."""
    from aristos_council.pipeline import run_rank_pipeline
    return run_rank_pipeline(
        None, "financials_v1", universe_id="financials_16_v1", universes_dir=UNIV_DIR,
        strategies_dir=STRAT_DIR, ranker_only=True, adapter=_FinAdapter(low_cap=low_cap),
        today=date(2026, 6, 30), freeze_dir=runs_dir)


def _fin_check(ticker, runs_dir, *, reference="financials_16_v1", low_cap=()):
    return run_company_check(
        ticker, "financials_v1", reference, adapter=_FinAdapter(low_cap=low_cap),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=runs_dir,
        today=date(2026, 6, 30))


def test_verdict_of_record_quotes_verdict_and_rank(tmp_path):
    runs = tmp_path / "runs"
    _freeze_financials_run(runs)
    r = _fin_check("GS", runs)
    # GS ranks strictly last of all 16 -> bottom quintile SELL, rank 16 of 16, quoted
    # verbatim (never recomputed): sourced from the frozen run, dated by its manifest.
    # RANK-DISPLAY-1: the ordinal position leads, with the rank-SUM as detail against its
    # best/worst bounds (F factors -> best F · worst F×16) so it is never misread.
    f = len(r.factors)
    assert r.verdict_of_record.startswith(
        f"in the latest frozen run of financials_16_v1 (run {r.reference_run_date}): "
        f"SELL, rank 16 of 16 · score ")
    assert r.verdict_of_record.endswith(f"(best {f} · worst {f * 16}).")
    text = format_company_check(r)
    assert f"VERDICT OF RECORD: {r.verdict_of_record}" in text
    assert _no_verdict(text)                        # still never ISSUES a verdict


def test_verdict_of_record_quotes_exclusion_with_reason(tmp_path):
    runs = tmp_path / "runs"
    _freeze_financials_run(runs, low_cap=("MET",))     # MET below the 5B cap -> excluded
    r = _fin_check("MET", runs, low_cap=("MET",))
    assert r.verdict_of_record == (
        f"excluded in the latest frozen run of financials_16_v1 "
        f"({r.reference_run_date}) — below min market cap.")
    assert f"VERDICT OF RECORD: {r.verdict_of_record}" in format_company_check(r)


def test_verdict_of_record_renders_nothing_without_reference(tmp_path):
    runs = tmp_path / "runs"
    _freeze_financials_run(runs)
    r = _fin_check("GS", runs, reference="")           # no reference selected -> nothing
    assert r.verdict_of_record is None
    assert "VERDICT OF RECORD" not in format_company_check(r)


def test_verdict_of_record_renders_nothing_for_name_not_in_run(tmp_path):
    runs = tmp_path / "runs"
    _freeze_financials_run(runs)
    # AAPL isn't in financials_16_v1's frozen cohort -> no verdict of record to quote.
    r = _fin_check("AAPL", runs)
    assert r.verdict_of_record is None
    assert "VERDICT OF RECORD" not in format_company_check(r)


def test_verdict_of_record_renders_nothing_when_no_frozen_run(tmp_path):
    runs = tmp_path / "runs"                            # empty -> no frozen run exists
    runs.mkdir()
    r = _fin_check("GS", runs)
    assert r.verdict_of_record is None
    assert "VERDICT OF RECORD" not in format_company_check(r)


def test_closing_boilerplate_swaps_both_ways(tmp_path):
    runs = tmp_path / "runs"
    _freeze_financials_run(runs)
    # No reference -> boilerplate STAYS ("a verdict requires a universe run").
    no_ref = _fin_check("GS", runs, reference="")
    assert "a verdict requires a universe run" in no_ref.pointer
    assert "VERDICT OF RECORD above" not in no_ref.pointer
    # A quoted verdict of record -> boilerplate REPLACED by a pointer to the record.
    quoted = _fin_check("GS", runs)
    assert quoted.verdict_of_record is not None
    assert "a verdict requires a universe run" not in quoted.pointer
    assert "VERDICT OF RECORD above" in quoted.pointer


def test_factor_lines_byte_unchanged_when_record_renders(tmp_path):
    from aristos_council.company_check import format_factor_value
    runs = tmp_path / "runs"
    _freeze_financials_run(runs)
    r = _fin_check("GS", runs)
    text = format_company_check(r)
    # Every FACTOR VALUES line renders byte-for-byte as before — the new VERDICT OF RECORD
    # line is separate and does not disturb them.
    for fc in r.factors:
        line = (f"  {fc.label} ({fc.factor}): "
                f"{format_factor_value(fc.factor, fc.value)} "
                f"[{fc.source}] — {fc.context}")
        assert line in text
        assert "VERDICT OF RECORD" not in line
    assert "VERDICT OF RECORD:" in text
