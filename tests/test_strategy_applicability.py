"""STRAT-PICKER-1 — the picker must offer every lens that CAN grade the cohort.

Live incident (2026-08-10): an ad-hoc stock cohort (``adhoc:507e10cf``, 21 names) was
offered ONE lens while RAW / flagship / GARP v2 / Defensive / Financials all sat in
``strategies/``. An ad-hoc cohort declares no asset class, so the correct behavior is to
filter NOTHING (three-valued: ``etf`` / ``equity`` / UNKNOWN, and UNKNOWN never hides).

Pinned against the LIVE ``strategies/`` dir — the classification is DERIVED from the YAMLs
(a lens's ``asset_kinds`` × the cohorts it suggests), so a new lens is covered with no
change here.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.demo_surface import is_hidden_strategy
from aristos_council.strategy.applicability import (
    applicable_rank_strategies,
    cohort_asset_kind,
    is_applicable,
    out_of_scope_note,
    strategy_asset_kinds,
)
from aristos_council.strategy.discovery import rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# The five STOCK lenses the live picker must offer for a stock cohort (the incident list:
# RAW, flagship, GARP v2, Defensive, Financials).
_STOCK_LENSES = {"magic_formula_raw_v1", "magic_formula_momentum_v1", "growth_garp_v2",
                 "conservative_plus_v1", "financials_v1"}
_ETF_LENSES = {"etf_core_v1", "etf_dividend_v1", "etf_growth_v1"}


def _visible_rank():
    """The rank strategies a default (validation-toggle-off) picker starts from."""
    return [load_rank_strategy(s.path) for s in rank_strategies(STRAT_DIR)
            if not is_hidden_strategy(s)]


def _ids(strategies) -> set[str]:
    return {s.id for s in strategies}


# --------------------------------------------------------------------------- #
# The incident: an ad-hoc cohort declares nothing, so nothing may be hidden
# --------------------------------------------------------------------------- #
def test_adhoc_cohort_asset_kind_is_unknown():
    assert cohort_asset_kind("adhoc:507e10cf", _visible_rank()) is None
    assert cohort_asset_kind(None, _visible_rank()) is None
    assert cohort_asset_kind("", _visible_rank()) is None


def test_adhoc_stock_cohort_offers_every_stock_lens():
    visible = _visible_rank()
    offered = _ids(applicable_rank_strategies(
        visible, cohort_asset_kind("adhoc:507e10cf", visible)))
    assert _STOCK_LENSES <= offered           # the five the live picker withheld
    assert offered == _ids(visible)           # UNKNOWN filters NOTHING


def test_hidden_strategies_stay_hidden_for_an_adhoc_cohort():
    # Visibility is the caller's filter and runs FIRST — applicability never revives a
    # superseded config (magic_formula_v1 / growth_garp_v1 are ui: hidden).
    visible = _visible_rank()
    offered = _ids(applicable_rank_strategies(visible, None))
    assert "magic_formula_v1" not in offered
    assert "growth_garp_v1" not in offered


# --------------------------------------------------------------------------- #
# Regression: a declared ETF cohort still shows ONLY the ETF lenses
# --------------------------------------------------------------------------- #
def test_etf_cohorts_are_derived_and_offer_only_etf_lenses():
    visible = _visible_rank()
    for etf_universe in ("etf_core_ucits_v1", "etf_dividend_us_v1", "etf_growth_us_v1"):
        kind = cohort_asset_kind(etf_universe, visible)
        assert kind == "etf", etf_universe
        offered = _ids(applicable_rank_strategies(visible, kind))
        assert offered <= _ETF_LENSES, etf_universe
        assert not (offered & _STOCK_LENSES), etf_universe


def test_equity_cohorts_are_derived_and_exclude_the_etf_lenses():
    visible = _visible_rank()
    kind = cohort_asset_kind("growth_40_v1", visible)
    assert kind == "equity"
    offered = _ids(applicable_rank_strategies(visible, kind))
    assert _STOCK_LENSES <= offered
    assert not (offered & _ETF_LENSES)


def test_a_cohort_no_lens_suggests_is_unknown_not_empty():
    visible = _visible_rank()
    # a saved personal list nothing suggests: undeclared, so every lens stays offered
    assert cohort_asset_kind("my_portfolio_v1", visible) is None
    assert _ids(applicable_rank_strategies(visible, None)) == _ids(visible)


# --------------------------------------------------------------------------- #
# The primitives
# --------------------------------------------------------------------------- #
def test_declared_kinds_are_normalized_and_absent_kinds_scope_nothing():
    momentum = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")
    assert strategy_asset_kinds(momentum) == frozenset({"equity"})

    class _Unscoped:
        id = "unscoped_v1"

    assert strategy_asset_kinds(_Unscoped()) == frozenset()
    assert is_applicable(_Unscoped(), "etf")          # scopes nothing -> always applicable
    assert is_applicable(_Unscoped(), None)


def test_out_of_scope_note_names_the_mismatch_and_is_silent_when_fine():
    momentum = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")
    note = out_of_scope_note(momentum, "etf")
    assert "equity" in note and "etf" in note
    assert out_of_scope_note(momentum, "equity") == ""
    assert out_of_scope_note(momentum, None) == ""     # UNKNOWN never warns
