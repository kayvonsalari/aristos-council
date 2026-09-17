"""ASSET-MODE-1 — one Stocks / ETFs switch, and the UI shows stocks by default.

Council Station is a stock-analysis tool in daily use, and the three ETF lenses plus five
ETF lists sat in every picker regardless. They rank on fee, size, trend and payout —
published for every fund and genuinely useful — but they are the minority job, and a
picker that offers them always makes the majority job slower.

HIDDEN, never deleted, and that distinction is what most of this file guards. Past client
ETF work ran on these lenses and a planned ETF-only list of held funds still needs them,
so this is a VISIBILITY filter and nothing else: no strategy, universe, criterion, factor,
rank, verdict, report, CLI or Colab path changes, and the asset-kind gate that already
walls ETFs out of stock lenses is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

from aristos_council.demo_surface import (ASSET_KINDS, ASSET_MODES, DEFAULT_ASSET_MODE,
                                          ETFS, STOCKS, asset_mode_filter, is_etf_lens,
                                          undecided_list, universe_asset_kind)

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Lens:
    asset_kinds: list = field(default_factory=list)
    thesis: Optional[list] = None
    kind: str = "selector"


@dataclass
class _List:
    tickers: list = field(default_factory=list)
    thesis: str = ""
    asset_kind: str = ""


# --------------------------------------------------------------------------- #
# Which lens is an ETF lens
# --------------------------------------------------------------------------- #
# THE RULE THAT APPLIES HERE: this repo's strategies DO declare `thesis`, so both tests
# are live — asset_kinds contains `etf` and not `equity`, OR thesis includes `funds`.
# The structural one is authoritative: a lens is an ETF lens because its FACTORS read
# fund attributes, which is why the gate admits only funds to it.

def test_an_etf_lens_is_one_whose_factors_read_fund_attributes():
    assert is_etf_lens(_Lens(asset_kinds=["etf"], thesis=["funds"]))
    assert is_etf_lens(_Lens(asset_kinds=["etf"]))            # structure alone
    assert is_etf_lens(_Lens(asset_kinds=[], thesis=["funds"]))  # declaration alone


def test_an_equity_lens_is_not():
    assert not is_etf_lens(_Lens(asset_kinds=["equity"], thesis=["value"]))
    assert not is_etf_lens(_Lens(asset_kinds=["equity"], thesis=["income"]))


def test_a_lens_admitting_BOTH_kinds_is_not_an_ETF_lens():
    """It reads company accounts as well, so it belongs where the companies are."""
    assert not is_etf_lens(_Lens(asset_kinds=["equity", "etf"]))


def test_an_UNRESTRICTED_check_lens_stays_on_the_stocks_side():
    """Forensic declares no thesis and reads company accounts. Nothing stops it being
    pointed at a fund, and that is exactly why it must not be offered in ETFs mode: its
    factors would abstain on every name and the run would say nothing."""
    assert not is_etf_lens(_Lens(asset_kinds=[], thesis=None))
    assert not is_etf_lens(_Lens(asset_kinds=["equity"], thesis=None, kind="check"))


def test_a_scalar_thesis_is_read_the_same_as_a_list():
    """Strategies carry a list; a universe carries a string. One reader for both."""
    assert is_etf_lens(_Lens(asset_kinds=[], thesis="funds"))


# --------------------------------------------------------------------------- #
# Which list is an ETF list
# --------------------------------------------------------------------------- #
ETF_TICKERS = {"XLE", "VDE", "SPY", "VOO"}


def test_an_explicit_field_decides_it():
    assert universe_asset_kind(_List(asset_kind="etfs", tickers=["XOM"])) == "etfs"
    assert universe_asset_kind(_List(asset_kind="stocks", tickers=["SPY"])) == "stocks"


def test_the_explicit_field_BEATS_every_inference():
    """A person who marked a list has answered the question. An all-ETF list marked
    stocks stays on the stocks side, and a funds-thesis list marked stocks does too —
    otherwise the field is decoration."""
    all_funds = _List(asset_kind="stocks", thesis="funds",
                      tickers=["SPY", "VOO"])
    assert universe_asset_kind(all_funds, etf_tickers=ETF_TICKERS) == "stocks"


def test_a_funds_thesis_marks_it_when_the_field_is_absent():
    assert universe_asset_kind(_List(thesis="funds", tickers=["ANY"])) == "etfs"


def test_a_list_of_only_known_funds_is_inferred_as_etfs():
    assert universe_asset_kind(_List(tickers=["XLE", "VDE"]),
                               etf_tickers=ETF_TICKERS) == "etfs"


def test_ONE_unknown_ticker_stops_the_all_funds_inference():
    """All, not most: a list that is mostly funds is not a fund list, it is a mixed one,
    and guessing would put real companies behind a switch that hides them."""
    assert universe_asset_kind(_List(tickers=["XLE", "XOM"]),
                               etf_tickers=ETF_TICKERS) == "stocks"


def test_an_ordinary_stock_list_falls_to_stocks_and_is_NOT_undecided():
    """Every ticker absent from the fund set IS a decision, not a failure to decide."""
    plain = _List(tickers=["XOM", "CVX"])
    assert universe_asset_kind(plain, etf_tickers=ETF_TICKERS) == "stocks"
    assert not undecided_list(plain, etf_tickers=ETF_TICKERS)


def test_a_MIXED_list_is_undecided_and_defaults_to_stocks():
    """Neither side of the switch is right for it, so it is named once rather than filed
    silently — and it defaults to stocks, where a mixed list is least wrong."""
    mixed = _List(tickers=["XLE", "XOM"])
    assert universe_asset_kind(mixed, etf_tickers=ETF_TICKERS) == "stocks"
    assert undecided_list(mixed, etf_tickers=ETF_TICKERS)


def test_an_explicitly_marked_list_is_never_undecided():
    assert not undecided_list(_List(asset_kind="stocks", tickers=["XLE", "XOM"]),
                              etf_tickers=ETF_TICKERS)


# --------------------------------------------------------------------------- #
# The filter pair
# --------------------------------------------------------------------------- #
def test_stocks_mode_shows_stock_lenses_and_stock_lists():
    lens_ok, list_ok = asset_mode_filter(STOCKS, etf_tickers=ETF_TICKERS)
    assert lens_ok(_Lens(asset_kinds=["equity"]))
    assert not lens_ok(_Lens(asset_kinds=["etf"]))
    assert list_ok(_List(tickers=["XOM"]))
    assert not list_ok(_List(thesis="funds", tickers=["SPY"]))


def test_etfs_mode_shows_exactly_the_other_side():
    lens_ok, list_ok = asset_mode_filter(ETFS, etf_tickers=ETF_TICKERS)
    assert lens_ok(_Lens(asset_kinds=["etf"]))
    assert not lens_ok(_Lens(asset_kinds=["equity"]))
    assert list_ok(_List(thesis="funds", tickers=["SPY"]))
    assert not list_ok(_List(tickers=["XOM"]))


def test_the_two_modes_PARTITION_everything():
    """Nothing may fall between them: a lens or list hidden in both modes is unreachable
    from the app, which is deletion by another name."""
    lenses = [_Lens(asset_kinds=["equity"]), _Lens(asset_kinds=["etf"]),
              _Lens(asset_kinds=[], thesis=None), _Lens(asset_kinds=["equity", "etf"])]
    lists = [_List(tickers=["XOM"]), _List(thesis="funds", tickers=["SPY"]),
             _List(asset_kind="etfs", tickers=["X"]), _List(tickers=["XLE", "XOM"])]
    stock_lens, stock_list = asset_mode_filter(STOCKS, etf_tickers=ETF_TICKERS)
    etf_lens, etf_list = asset_mode_filter(ETFS, etf_tickers=ETF_TICKERS)
    for lens in lenses:
        assert stock_lens(lens) != etf_lens(lens)
    for lst in lists:
        assert stock_list(lst) != etf_list(lst)


def test_an_unknown_mode_falls_back_to_stocks():
    """The safe answer for a value nobody set is the default everywhere else."""
    for mode in ("", None, "funds", "Everything"):
        lens_ok, _ = asset_mode_filter(mode, etf_tickers=ETF_TICKERS)
        assert lens_ok(_Lens(asset_kinds=["equity"]))
        assert not lens_ok(_Lens(asset_kinds=["etf"]))


def test_the_mode_string_is_matched_case_insensitively():
    lens_ok, _ = asset_mode_filter("etfs", etf_tickers=ETF_TICKERS)
    assert lens_ok(_Lens(asset_kinds=["etf"]))


# --------------------------------------------------------------------------- #
# The shipped assets
# --------------------------------------------------------------------------- #
def _rank_strategy(sid):
    from aristos_council.strategy.rank_loader import load_rank_strategy

    return load_rank_strategy(ROOT / "strategies" / f"{sid}.yaml")


def _shipped_universes():
    """The manifests the REPO ships — the top-level directory only. ``universes/local/``
    is gitignored portfolio-class data that differs from machine to machine, so a test
    that asserted anything about it would pass here and fail on a clean checkout."""
    from aristos_council.universe import list_universes

    return [u for u in list_universes(ROOT / "universes") if not u.local]


def test_every_shipped_ETF_lens_is_hidden_in_stocks_and_shown_in_ETFs():
    stock_lens, _ = asset_mode_filter(STOCKS)
    etf_lens, _ = asset_mode_filter(ETFS)
    for sid in ("etf_core_v1", "etf_dividend_v1", "etf_growth_v1"):
        strategy = _rank_strategy(sid)
        assert not stock_lens(strategy), sid
        assert etf_lens(strategy), sid


def test_every_shipped_STOCK_lens_is_shown_in_stocks_and_hidden_in_ETFs():
    stock_lens, _ = asset_mode_filter(STOCKS)
    etf_lens, _ = asset_mode_filter(ETFS)
    for sid in ("conservative_plus_v1", "cyclical_income_v1", "magic_formula_raw_v1",
                "forensic_v1", "financials_v1", "growth_garp_v2"):
        strategy = _rank_strategy(sid)
        assert stock_lens(strategy), sid
        assert not etf_lens(strategy), sid


def test_every_shipped_ETF_universe_resolves_to_etfs():
    shipped = _shipped_universes()
    assert shipped, "no shipped universes found"
    for manifest in shipped:
        assert universe_asset_kind(manifest) == "etfs", manifest.id


def test_the_shipped_ETF_universes_say_so_EXPLICITLY():
    """They are the one set nobody should have to infer: the app ships them, so the app
    can state what they are. Inference stays for lists a person made."""
    for manifest in _shipped_universes():
        assert manifest.asset_kind == "etfs", manifest.id


# --------------------------------------------------------------------------- #
# The field's vocabulary
# --------------------------------------------------------------------------- #
def test_the_validator_rejects_an_unknown_asset_kind():
    """A typo would otherwise read as "not marked" and file the list under stocks —
    exactly the silent wrong answer this field exists to prevent."""
    from pydantic import ValidationError

    from aristos_council.universe import Universe

    for bad in ("etf", "funds", "Stock", "equities"):
        with pytest.raises(ValidationError):
            Universe(id="x_v1", tickers=["XOM"], asset_kind=bad)


def test_the_validator_accepts_the_two_it_offers_in_any_case():
    from aristos_council.universe import Universe

    assert Universe(id="x_v1", tickers=["XOM"], asset_kind="ETFs").asset_kind == "etfs"
    assert Universe(id="x_v1", tickers=["XOM"], asset_kind="stocks").asset_kind == "stocks"
    assert Universe(id="x_v1", tickers=["XOM"]).asset_kind == ""      # optional


def test_the_vocabulary_is_the_switch_s_two_sides():
    assert ASSET_KINDS == ("stocks", "etfs")
    assert ASSET_MODES == ("Stocks", "ETFs")
    assert DEFAULT_ASSET_MODE == STOCKS
