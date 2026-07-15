"""ETF-1 ITEM 4 — the two all-US ETF universes.

Live adapter resolution (every ticker resolves, 251 closes) was verified by the ITEM-1
probe (committed under reports/exploratory/); no ticker was dropped. These tests pin the
manifests' shape offline.
"""

from pathlib import Path

from aristos_council.universe import load_universe_by_id

UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"
STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

DIVIDEND = ["VIG", "VYM", "SCHD", "DVY", "SDY", "NOBL", "HDV", "SPYD", "DGRO", "FVD"]
GROWTH = ["VUG", "QQQ", "IWF", "SPYG", "SCHG", "VONG", "MGK", "IWY"]


def test_dividend_universe_loads_with_all_ten_tickers():
    u = load_universe_by_id("etf_dividend_us_v1", UNIV_DIR)
    assert u.tickers == DIVIDEND
    assert u.display_name == "Dividend ETFs (US)"


def test_growth_universe_loads_with_all_eight_tickers():
    u = load_universe_by_id("etf_growth_us_v1", UNIV_DIR)
    assert u.tickers == GROWTH
    assert u.display_name == "Growth ETFs (US)"


def test_universe_ids_carry_all_us_rationale():
    for uid in ("etf_dividend_us_v1", "etf_growth_us_v1"):
        u = load_universe_by_id(uid, UNIV_DIR)
        assert "all-us" in u.rationale.lower()


def test_lenses_suggest_their_universes():
    from aristos_council.strategy.rank_loader import load_rank_strategy
    div = load_rank_strategy(STRAT_DIR / "etf_dividend_v1.yaml")
    grw = load_rank_strategy(STRAT_DIR / "etf_growth_v1.yaml")
    # the suggested universe resolves to a real manifest (a hierarchy, never a lock)
    assert load_universe_by_id(div.suggested_universes[0], UNIV_DIR).id == \
        "etf_dividend_us_v1"
    assert load_universe_by_id(grw.suggested_universes[0], UNIV_DIR).id == \
        "etf_growth_us_v1"
