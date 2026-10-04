"""CAGR-CRASH-1 (Batch 17 item 2) — a loss year must never take the Company Check page down.

Live: Novocure (NVCR.US) and Ford (F) crashed with ``ValueError: Unknown format code '%' for
object of type 'complex'`` — a positive start and a NEGATIVE END gives (neg / pos) ** (1/n),
a complex number in Python, and the label's ``:+.1%`` raised.
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council import abs_readings
from aristos_council.abs_readings import GROWTH_WINDOWS, growth_record, guard
from aristos_council.company_check import format_company_check
from aristos_council.data.adapter import Fundamentals

from tests.test_company_check import STRAT_DIR, UNIV_DIR, RUNS_DIR, _OneName, _STRAT


def _eps(newest_first):
    return Fundamentals(ticker="X", currency="USD", financial_currency="USD",
                        aligned_annual={"diluted_eps": list(newest_first)},
                        total_revenue=[100.0, 90, 80, 70, 60, 50, 45, 40, 35, 30, 25])


_ELEVEN = [2.0] * 11

CASES = {
    "negative end": [-1.0] + [2.0] * 10,
    "both negative": [-1.0] + [2.0] * 4 + [-3.0] + [2.0] * 5,
    "zero start": [2.0] * 5 + [0.0] + [2.0] * 4 + [0.0],   # zero 5 AND 10 years back
    "zero end": [0.0] + [2.0] * 10,
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_compound_rate_when_start_or_end_is_not_positive(name):
    series = CASES[name]
    rec = growth_record(_eps(series))
    for window in GROWTH_WINDOWS:           # the 5- AND the 10-year window
        r = rec.eps.cagr[window]
        assert r.value is None, f"{name}: {window}y should abstain"
        assert r.text().startswith("not stated — "), r.text()
        assert not r.failure                 # an honest abstention, not a swallowed crash
    assert "%" not in " ".join(ln for ln in rec.eps.lines() if "compounded" in ln)


def test_a_positive_series_still_compounds():
    r = growth_record(_eps(_ELEVEN)).eps.cagr[5]
    assert r.value == pytest.approx(0.0)
    assert "compounded +0.0% a year over 5 years" in r.text()


def test_a_reading_that_raises_becomes_not_available_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise ValueError("Unknown format code '%' for object of type 'complex'")
    monkeypatch.setattr(abs_readings, "_cagr", boom)
    rec = growth_record(_eps(_ELEVEN))
    text = " ".join(rec.lines())
    assert "not available: ValueError" in text


@pytest.mark.parametrize("kind", ["debt_and_cash", "growth_record", "analyst_trend",
                                  "price_and_cash"])
def test_guard_returns_a_same_typed_placeholder(kind):
    def boom():
        raise RuntimeError("kaboom")
    out = guard(kind, boom)
    assert out is not None
    if kind in ("debt_and_cash", "growth_record", "price_and_cash"):
        assert "not available: RuntimeError: kaboom" in " ".join(out.lines())
    else:
        assert "not available" in out.headline


def test_page_level_a_raising_reading_still_renders_the_report(monkeypatch):
    from aristos_council.company_check import run_company_check

    def boom(*a, **k):
        raise ValueError("Unknown format code '%' for object of type 'complex'")
    monkeypatch.setattr(abs_readings, "growth_record", boom)
    monkeypatch.setattr(abs_readings, "debt_and_cash", boom)
    f = _eps(_ELEVEN)
    res = run_company_check("X", _STRAT, "", adapter=_OneName(f), strategies_dir=STRAT_DIR,
                            universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
                            today=date(2026, 6, 30))
    text = format_company_check(res)
    assert "not available: ValueError" in text
    assert "SCREEN" in text and "FACTOR VALUES" in text      # the rest of the page rendered
