"""BANK-PAGE-1 (JPM with EPV, Financials, Forensic, Growth, Magic Formula RAW, Quality).

(a) A voting lens with no backtest for the company's cohort says "untested here" (covered where the
    old "no cohort leaves every badge None" test used to be: tests/test_company_report.py).
(b) Growth no longer ranks a bank or an insurer on return on capital and revenue growth.
(c) "holds $183.1bn more cash than debt" / free cash flow "$-147.8bn" mean nothing for a bank."""
from __future__ import annotations

from aristos_council.abs_readings import (NOT_MEANINGFUL_FOR_FINANCIALS, debt_and_cash,
                                          is_financial_sector, price_and_cash)
from aristos_council.data.adapter import Fundamentals
from aristos_council.strategy.rank_loader import load_rank_strategy
from tests.test_company_report import STRAT_DIR


def _f(sector, **kw):
    base = dict(ticker="JPM", name="JPMorgan", company_name="JPMorgan Chase", sector=sector,
                currency="USD", financial_currency="USD", total_debt=500e9, total_cash=683e9,
                operating_cash_flow=-120e9, free_cash_flow=-147.8e9,
                operating_income=[50e9], total_revenue=[170e9])
    base.update(kw)
    return Fundamentals(**base)


# --- (c) -------------------------------------------------------------------------------------- #
def test_a_bank_gets_one_not_meaningful_line_instead_of_cash_beyond_debt():
    lines = debt_and_cash(_f("Financial Services")).lines()
    assert lines == [NOT_MEANINGFUL_FOR_FINANCIALS]
    assert NOT_MEANINGFUL_FOR_FINANCIALS == ("not meaningful for banks and insurers (deposits "
                                             "and loans are the business)")
    joined = " ".join(lines)
    assert "more cash than debt" not in joined and "no net debt" not in joined


def test_an_insurer_is_treated_the_same_way():
    assert debt_and_cash(_f("Financials")).lines() == [NOT_MEANINGFUL_FOR_FINANCIALS]


def test_a_bank_free_cash_flow_line_says_not_meaningful():
    pac = price_and_cash(None, _f("Financial Services"))
    assert NOT_MEANINGFUL_FOR_FINANCIALS in pac.lines()
    assert not any("free cash flow, oldest first" in ln for ln in pac.lines())


def test_a_non_financial_company_is_unchanged():
    lines = debt_and_cash(_f("Technology")).lines()
    assert any("more cash than debt" in ln for ln in lines)
    assert NOT_MEANINGFUL_FOR_FINANCIALS not in lines


def test_a_missing_sector_is_never_treated_as_a_bank():
    assert not is_financial_sector(_f(None)) and not is_financial_sector(None)
    assert any("more cash than debt" in ln for ln in debt_and_cash(_f(None)).lines())


# --- (b) -------------------------------------------------------------------------------------- #
def test_growth_excludes_banks_and_insurers_and_says_so_in_its_caption():
    growth = load_rank_strategy(STRAT_DIR / "growth_garp_v2.yaml")
    assert {"financial services", "financials"} <= {s.lower() for s in growth.exclude_sectors}
    assert growth.asks.endswith("for companies worth at least $5bn (not banks or insurers).")


def test_growth_gate_fires_on_a_bank_in_the_rank_stage():
    from datetime import date

    from aristos_council.pipeline import run_rank_pipeline
    from tests.test_company_report import _Adapter

    class _BankAdapter(_Adapter):
        def get_fundamentals(self, ticker):
            f = super().get_fundamentals(ticker)
            if ticker == "CO":
                from dataclasses import replace
                f = replace(f, sector="Financial Services")
            return f

    tickers = ["CO"] + [f"P{i:02d}" for i in range(13)]
    res = run_rank_pipeline(tickers, "growth_garp_v2", ranker_only=True, adapter=_BankAdapter(),
                            today=date(2026, 6, 30), strategies_dir=STRAT_DIR, use_cache=False)
    assert ("CO", "sector excluded (Financial Services)") in res.excluded


def test_the_other_growth_version_and_other_lenses_are_untouched():
    old = load_rank_strategy(STRAT_DIR / "growth_garp_v1.yaml")
    assert not old.exclude_sectors
    raw = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")
    assert "Utilities" in raw.exclude_sectors


def test_no_cohort_says_untested_once_not_twice():
    from types import SimpleNamespace

    from aristos_council.company_report import (NO_COHORT_TRACK_RECORD_LINE, CompanyReport,
                                                LensVote, attach_track_record)
    votes = [LensVote("financials_v1", "Financials", status="ranked", verdict="hold",
                      position=5, cohort_size=14)]
    subject = SimpleNamespace(industry="Banks - Diversified", gics_subindustry="Diversified Banks")
    check = SimpleNamespace(peer_group=SimpleNamespace(subject=subject))
    report = CompanyReport(ticker="JPM", check=check, votes=votes)
    attach_track_record(report)
    assert report.track_record_caption == NO_COHORT_TRACK_RECORD_LINE
    assert report.track_record_summary == ""
    assert report.votes[0].badge_suffix == " (untested here)"
