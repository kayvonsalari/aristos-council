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


def _check(fundamentals, *, ticker="X", has_price=True, reference=""):
    return run_company_check(
        ticker, _STRAT, reference, adapter=_OneName(fundamentals, has_price=has_price),
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
    # ALL criteria present (min_roic FAIL, min_market_cap PASS) — evaluated, not short-circuited.
    statuses = {c.name: c.status for c in r.screen}
    assert statuses["min_roic"] == "FAIL"
    assert statuses["min_market_cap"] == "PASS"
    # every rank factor is reported with a raw value + context (no reference -> raw).
    assert {f.factor for f in r.factors} == {"roic", "earnings_yield", "momentum_12m"}
    assert all("no reference run available" in f.context for f in r.factors)
    # the price/fundamentals divergence flag fires (min_roic fail + momentum >= +0.30).
    assert r.divergence_flag is not None and "price diverging" in r.divergence_flag
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
    sector="Technology", ebit=[4000.0], pe_ratio=18.0,
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
