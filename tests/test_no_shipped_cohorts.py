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

import textwrap
from pathlib import Path

from aristos_council.universe import LOCAL_SUBDIR, list_universes

ROOT = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = ROOT / "universes"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "universes"

_DELETED = ("growth_40_v1", "defensive_16_v1", "defensive_income_16_v1",
            "energy_watch_v1", "financials_16_v1")


def _shipped_ids(universes_dir: Path) -> set[str]:
    """The ids of the SHIPPED manifests only — the top-level folder, excluding the
    gitignored ``local/`` personal lists (marked ``local=True`` by ``list_universes``).
    This test claims about what the app SHIPS, so it must not read a set that varies with
    whatever personal lists happen to sit in ``universes/local/`` on this machine — a
    restored personal ``growth_40_v1`` is correct and must not fail a shipped-cohorts
    assertion."""
    return {u.id for u in list_universes(universes_dir) if not u.local}


def test_the_shipped_demo_cohorts_are_gone_from_the_product_surface():
    shipped = _shipped_ids(UNIVERSES_DIR)                  # SHIPPED only (not local/)
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
    # tickers is a strategy you cannot run. Subset check, so personal lists never break it.
    shipped = {u.id for u in list_universes(UNIVERSES_DIR)}
    assert {"etf_dividend_us_v1", "etf_growth_us_v1", "etf_dividend_ucits_v1",
            "etf_growth_ucits_v1", "etf_core_ucits_v1"} <= shipped


def test_a_personal_list_may_reuse_a_deleted_cohort_id_and_is_still_offered(tmp_path):
    """The real contract (the defect these fixes address): a PERSONAL list saved under a
    deleted cohort's id is legitimate — it must still be OFFERED in the selector, while the
    shipped-cohorts guarantee (no such id SHIPPED) still holds. Both are asserted against an
    isolated dir, so the result never depends on this machine's ``universes/local/``."""
    (tmp_path / LOCAL_SUBDIR).mkdir()
    # a shipped ETF list (top-level) + a personal list reusing a DELETED cohort's id.
    (tmp_path / "etf_dividend_us_v1.yaml").write_text(textwrap.dedent("""\
        id: etf_dividend_us_v1
        display_name: Dividend ETFs (US)
        tickers: [SCHD, VYM]
        """), encoding="utf-8")
    (tmp_path / LOCAL_SUBDIR / "growth_40_v1.yaml").write_text(textwrap.dedent("""\
        id: growth_40_v1
        display_name: Growth 40
        tickers: [AAPL, MSFT]
        """), encoding="utf-8")

    # shipped-cohorts guarantee still holds: the reused id is NOT a shipped id.
    assert "growth_40_v1" not in _shipped_ids(tmp_path)
    assert not (tmp_path / "growth_40_v1.yaml").exists()   # nothing shipped under that id

    # …but the personal list IS offered by the selector's source (list_universes), tagged
    # local, so it reaches the picker.
    everything = {u.id: u for u in list_universes(tmp_path)}
    assert "growth_40_v1" in everything and everything["growth_40_v1"].local is True
    assert "etf_dividend_us_v1" in everything      # the shipped list is offered too
