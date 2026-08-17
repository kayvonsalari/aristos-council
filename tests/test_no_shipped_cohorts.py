"""The app ships no demo cohorts (FUND-UI-2).

A universe is a plain ticker list YOU save (``universes/local/``, gitignored). The five
shipped stock cohorts — Growth 40 and friends — were dormant data with no relevance to
anyone's actual holdings, and every one of them was offered in the run flow's list
picker ahead of the user's own lists. They are gone from the product surface; the
manifests survive as test fixtures (``tests/fixtures/universes/``) for the checks and
scripts that are only meaningful against those exact members.

The ETF lists stay: they are the only carrier of the UCITS/US fund tickers the ETF
strategies rank, and nobody types `IE00B4L5Y983`-class symbols from memory. They are
ordinary lists, not cohorts — editable and copyable like any other.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.universe import list_universes

ROOT = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = ROOT / "universes"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "universes"

_DELETED = ("growth_40_v1", "defensive_16_v1", "defensive_income_16_v1",
            "energy_watch_v1", "financials_16_v1")


def test_the_shipped_demo_cohorts_are_gone_from_the_product_surface():
    shipped = {u.id for u in list_universes(UNIVERSES_DIR)}
    assert not (shipped & set(_DELETED))
    for uid in _DELETED:
        assert not (UNIVERSES_DIR / f"{uid}.yaml").exists()


def test_the_cohorts_survive_verbatim_as_fixtures():
    # Deleted from the product, NOT destroyed: past scoreboard rows and the validation
    # scripts still resolve them.
    fixtures = {u.id for u in list_universes(FIXTURES)}
    assert set(_DELETED) <= fixtures


def test_the_etf_lists_are_still_shipped():
    # Kept deliberately (see the module docstring): an ETF strategy with no list of fund
    # tickers is a strategy you cannot run.
    shipped = {u.id for u in list_universes(UNIVERSES_DIR)}
    assert {"etf_dividend_us_v1", "etf_growth_us_v1", "etf_dividend_ucits_v1",
            "etf_growth_ucits_v1", "etf_core_ucits_v1"} <= shipped
