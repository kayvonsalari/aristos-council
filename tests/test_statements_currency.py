"""STATEMENTS-CCY-1 - Vale (XVALO.MC / VALE), 2026-10-22: EV/EBIT 43.5x for a business near 11x.

yfinance labels Vale's accounts ``financialCurrency = BRL``. Its headline ``totalRevenue`` is BRL 218bn, but the
annual statements (revenue 38.4bn, EBIT 6.7bn, total debt 21.8bn) are in USD. The EBIT was then converted with the
BRL rate, five-and-a-half times too small. The two figures are the same business months apart, so a gap of three
times or more says they are not in one currency; every figure that mixes them abstains.
"""
from __future__ import annotations

from datetime import date

from aristos_council.data.adapter import Fundamentals
from aristos_council.data.yfinance_adapter import _statements_currency_note
from aristos_council.tools.quality_epv import net_debt_to_ebit
from aristos_council.tools.screening import net_debt_to_operating_income


def test_vale_headline_against_statement_revenue_is_flagged():
    assert "5.7x" in _statements_currency_note(218_068_992_000, [38_403_000_000.0, 38_056_000_000.0])


def test_ordinary_growth_and_missing_figures_are_not_flagged():
    assert _statements_currency_note(110e9, [100e9]) == ""
    assert _statements_currency_note(250e9, [100e9]) == ""          # 2.5x: a hypergrower, one currency
    assert _statements_currency_note(None, [100e9]) == ""
    assert _statements_currency_note(100e9, []) == ""


def test_net_debt_ratios_abstain_on_the_note():
    f = Fundamentals(ticker="X", total_debt=110e9, total_cash=30e9, ebit=[6.7e9], operating_income=[11e9],
                     statements_currency_note="not in one currency")
    assert net_debt_to_ebit(f) == (None, "not in one currency")
    assert net_debt_to_operating_income(f)[0] is None
    assert net_debt_to_ebit(Fundamentals(ticker="X", total_debt=110e9, total_cash=30e9, ebit=[6.7e9]))[0] is not None


def test_the_valuation_band_abstains_with_the_reason():
    from aristos_council.factors import _gather_valuation_band
    f = Fundamentals(ticker="X", currency="EUR", financial_currency="BRL", statements_currency_note="differ 5.7x")
    band = _gather_valuation_band(object(), "X", f, today=date(2026, 10, 22))
    assert "differ 5.7x" in band.note and not band.note.startswith("not evaluated")


def test_the_debt_page_does_not_divide_across_currencies():
    from aristos_council.abs_readings import debt_and_cash
    f = Fundamentals(ticker="X", currency="EUR", financial_currency="BRL", total_debt=110e9, total_cash=30e9,
                     free_cash_flow=2.8e9, operating_cash_flow=8.8e9, operating_income=[11e9],
                     statements_currency_note="differ 5.7x")
    d = debt_and_cash(f)
    assert not d.years_to_repay.available and "differ 5.7x" in d.years_to_repay.note
    assert not d.net_debt_to_ocf.available and not d.interest_cover.available
    assert d.net_debt.available                      # the headline pair is one currency with itself


# --- VALE-EPV-ABSTAIN-1 (2026-10-31 sweep) --------------------------------------------------------------
def test_the_note_uses_the_short_money_format_never_raw_digits():
    note = _statements_currency_note(215_830_003_712, [38_403_000_000], "USD")
    assert note.startswith("headline revenue $215.8bn vs latest statement $38.4bn, 5.6x apart")
    import re
    assert not re.search(r"\d{10,}|\d{1,3}(,\d{3}){3,}", note)


def _mixed(**kw):
    return Fundamentals(ticker="V", market_cap=50e9, total_debt=110e9, total_cash=30e9,
                        total_revenue=[38e9] * 5, ebit=[10e9] * 5, operating_income=[10e9] * 5,
                        statements_currency_note="headline revenue $215.8bn vs latest statement $38.4bn, 5.6x apart",
                        **kw)


def test_epv_ev_and_price_to_book_fallback_abstain_with_the_one_reason():
    from aristos_council.factors import FactorInputs, enterprise_value, epv_reading_for, _price_to_book
    from aristos_council.tools.quality_epv import earnings_power_value
    f = _mixed(shareholders_equity=[20e9])
    assert enterprise_value(f) is None and _price_to_book(FactorInputs(ticker="V", fundamentals=f)) is None
    r = earnings_power_value(f, 100e9)
    assert r.margin_of_safety is None and "5.6x apart" in r.note
    assert "5.6x apart" in epv_reading_for(FactorInputs(ticker="V", fundamentals=f, fx_failed=True)).note


def test_altman_z_abstains_on_the_note_even_when_the_currency_codes_agree():
    from aristos_council.tools.screening import altman_z_score
    f = _mixed(currency="USD", financial_currency="USD")
    r = altman_z_score(f)
    assert r.value is None and "5.6x apart" in r.note


def test_a_one_factor_lens_does_not_vote_a_mixed_currency_name_last():
    import tempfile
    from pathlib import Path
    from aristos_council.pipeline import _rank_stage
    from aristos_council.persistence.replay import FrozenAdapter
    from aristos_council.strategy.rank_loader import load_rank_strategy
    fix = Path(__file__).resolve().parent / "fixtures" / "report_sweep"
    base = FrozenAdapter(fix / "frozen")

    class Flagged:
        def __getattr__(self, n):
            return getattr(base, n)

        def get_fundamentals(self, ticker, *a, **k):
            f = base.get_fundamentals(ticker, *a, **k)
            if ticker == "CAMX.ST":
                import dataclasses
                f = dataclasses.replace(
                    f, statements_currency_note="headline revenue $215.8bn vs latest statement $38.4bn, 5.6x apart")
            return f
    import json
    from datetime import date
    today = date.fromisoformat(json.loads((fix / "meta.json").read_text())["recorded_on"])
    ranked, excluded, *_ = _rank_stage(["VCYT", "9969.HK", "CAMX.ST"], load_rank_strategy(Path(__file__).resolve().parents[1] / "strategies" / "epv_v1.yaml"), Flagged(),
                                       today=today)
    assert "CAMX.ST" not in [r.ticker for r in ranked]
    assert any(t == "CAMX.ST" and "5.6x apart" in why and why.startswith("not evaluated") for t, why in excluded), excluded
