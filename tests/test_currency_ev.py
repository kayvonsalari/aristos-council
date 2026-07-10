"""Currency-consistent EV earnings yield (VERIFY-2 ITEM 1).

For a foreign issuer held via a US listing, market_cap arrives in the price currency
(USD) while total_debt / total_cash / EBIT arrive in the accounts' currency. The EV
earnings yield used to mix them (NVO printed 0.3951 — kroner treated as dollars). The fix
converts debt/cash/EBIT into the price currency via an FX rate fetched through the SAME
adapter (cached, frozen, replayable); on a failed FX fetch it ABSTAINS, never mixes.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.factors import (
    CurrencyConversion, FactorInputs, gather_factor_inputs, _earnings_yield_outcome)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
_TODAY = date(2026, 7, 10)

# NVO fixture, from the real freeze-record payload (M in the spec; raw units here so the
# min_market_cap gate sees real dollars — the earnings-yield RATIO is scale-invariant).
_M = 1e6
_NVO = dict(ticker="NVO", name="Novo Nordisk", currency="USD", financial_currency="DKK",
            sector="Healthcare", ebit=[134747.0 * _M], market_cap=216284.0 * _M,
            total_debt=146382.0 * _M, total_cash=21626.0 * _M, pe_ratio=25.0)
# a US name: single currency, must be byte-unchanged.
_US = dict(ticker="ZZZ", name="US Co", currency="USD", financial_currency="USD",
           sector="Technology", ebit=[3000.0 * _M], market_cap=20000.0 * _M,
           total_debt=5000.0 * _M, total_cash=2000.0 * _M, pe_ratio=15.0)


def _bars(rate_or_close, n=260):
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=_TODAY - timedelta(days=n - 1 - i), open=rate_or_close,
                 high=rate_or_close, low=rate_or_close, close=rate_or_close,
                 adj_close=rate_or_close, volume=10) for i in range(n)])


class _FxAdapter(MarketDataAdapter):
    """Serves NVO (DKK accounts) + a US name + the DKKUSD=X FX pair @ 0.1452."""

    name = "fake"

    def __init__(self, fx_rate=0.1452, fx_raises=False):
        self._fx_rate = fx_rate
        self._fx_raises = fx_raises

    def get_fundamentals(self, ticker):
        return Fundamentals(**(_NVO if ticker == "NVO" else _US))

    def get_price_history(self, ticker, *, start, end):
        if ticker.endswith("=X"):
            if self._fx_raises:
                raise RuntimeError("fx fetch failed")
            return _bars(self._fx_rate, n=12)
        return _bars(100.0)

    def get_dividend_history(self, ticker, *, start, end):
        return []


# --------------------------------------------------------------------------- #
# The conversion itself
# --------------------------------------------------------------------------- #
def test_nvo_converts_to_honest_yield_with_tag_not_the_mixed_figure():
    f = Fundamentals(**_NVO)
    fx = CurrencyConversion(rate=0.1452, from_ccy="DKK", to_ccy="USD", as_of="2026-07-10")
    val, src = _earnings_yield_outcome(FactorInputs(ticker="NVO", fundamentals=f, fx=fx))
    assert abs(val - 0.0835) < 0.001                      # ≈ 0.083, the honest figure
    assert abs(val - 0.3951) > 0.1                        # NOT the mixed-currency 0.3951
    assert src == "ev, DKK→USD @ 0.1452 (2026-07-10)"     # the conversion shows its work


def test_fx_failure_abstains_never_mixes():
    f = Fundamentals(**_NVO)
    val, src = _earnings_yield_outcome(
        FactorInputs(ticker="NVO", fundamentals=f, fx_failed=True))
    assert val is None and src == "abstained"


# --------------------------------------------------------------------------- #
# gather_factor_inputs fetches the FX rate through the adapter
# --------------------------------------------------------------------------- #
def test_gather_fetches_fx_and_converts():
    fi = gather_factor_inputs(_FxAdapter(), "NVO", today=_TODAY)
    assert fi.fx is not None and fi.fx.from_ccy == "DKK" and fi.fx.to_ccy == "USD"
    val, src = _earnings_yield_outcome(fi)
    assert abs(val - 0.0835) < 0.001 and "DKK→USD" in src


def test_same_currency_name_is_unchanged():
    fi = gather_factor_inputs(_FxAdapter(), "ZZZ", today=_TODAY)
    assert fi.fx is None and fi.fx_failed is False        # no FX fetched
    val, src = _earnings_yield_outcome(fi)
    # EBIT/EV computed with rate 1.0 == the pre-fix value; source stays the plain "ev" tag
    assert src == "ev"
    assert abs(val - (3000.0 / (20000.0 + 5000.0 - 2000.0))) < 1e-9


def test_gather_fx_fetch_failure_marks_fx_failed():
    fi = gather_factor_inputs(_FxAdapter(fx_raises=True), "NVO", today=_TODAY)
    assert fi.fx is None and fi.fx_failed is True
    assert _earnings_yield_outcome(fi)[0] is None          # abstains


# --------------------------------------------------------------------------- #
# Freeze + offline replay reproduces the conversion (FX rate frozen with inputs)
# --------------------------------------------------------------------------- #
def test_frozen_replay_reproduces_the_conversion(tmp_path):
    from aristos_council.pipeline import run_rank_pipeline

    runs = tmp_path / "runs"
    live = run_rank_pipeline(
        ["NVO", "ZZZ"], "magic_formula_raw_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_FxAdapter(), today=_TODAY, freeze_dir=runs)
    run_id = live.meta["run_id"]
    live_nvo = next(r.factor_values["earnings_yield"] for r in live.ranked
                    if r.ticker == "NVO")
    assert abs(live_nvo - 0.0835) < 0.001

    # replay OFFLINE from the frozen record (the FX pair was frozen too) — same value.
    replay = run_rank_pipeline(
        ["NVO", "ZZZ"], "magic_formula_raw_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, replay_run_id=run_id, freeze_dir=runs, today=_TODAY)
    replay_nvo = next(r.factor_values["earnings_yield"] for r in replay.ranked
                      if r.ticker == "NVO")
    assert replay_nvo == live_nvo                          # byte-for-byte offline
