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

# UCITS-1 — the two euro-investable UCITS cohorts (9 dividend + 6 growth).
DIVIDEND_UCITS = ["VHYL.L", "FUSD.L", "USDV.L", "ZPRG.DE", "TDIV.AS", "ISPA.DE",
                  "SEDY.L", "IDVY.AS", "SPYW.DE"]
GROWTH_UCITS = ["EQQQ.L", "CNDX.L", "XDEM.DE", "IWMO.L", "IUIT.L", "SMH.L"]


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


# --------------------------------------------------------------------------- #
# UCITS-1 — the two euro-investable UCITS universes
# --------------------------------------------------------------------------- #
def test_dividend_ucits_universe_loads_with_all_nine_tickers():
    u = load_universe_by_id("etf_dividend_ucits_v1", UNIV_DIR)
    assert u.tickers == DIVIDEND_UCITS
    assert u.display_name == "Dividend ETFs (UCITS)"
    assert u.role == "euro-investable exploration — observation only"


def test_growth_ucits_universe_loads_with_all_six_tickers():
    u = load_universe_by_id("etf_growth_ucits_v1", UNIV_DIR)
    assert u.tickers == GROWTH_UCITS
    assert u.display_name == "Growth ETFs (UCITS)"
    assert u.role == "euro-investable exploration — observation only"


def test_ucits_universes_document_static_layer_and_share_class_findings():
    div = load_universe_by_id("etf_dividend_ucits_v1", UNIV_DIR)
    grw = load_universe_by_id("etf_growth_ucits_v1", UNIV_DIR)
    div_text = (div.description + " " + div.rationale).lower()
    grw_text = (grw.description + " " + grw.rationale).lower()
    # euro-investable + slow fields static-layer/EODHD-sourced
    for text in (div_text, grw_text):
        assert "euro-investable" in text
        assert "static" in text and "eodhd" in text
    # acc share classes rank on a true zero distribution (product finding, not error)
    assert "true zero" in grw_text and "product finding" in grw_text
    # the dividend cohort's v2 should prefer DIST share classes
    assert "dist" in div_text and "v2" in div_text


def test_both_ucits_universes_discovered_in_selectors():
    from aristos_council.demo_surface import is_validation_universe
    from aristos_council.universe import list_universes
    manifests = list_universes(UNIV_DIR)
    by_id = {u.id: u for u in manifests}
    # both are discovered by the selector-backing listing...
    assert "etf_dividend_ucits_v1" in by_id
    assert "etf_growth_ucits_v1" in by_id
    # ...and are FRONT-STAGE (an "observation only" role is not the "never graded"
    # backstage marker), so a plain selector offers them.
    assert not is_validation_universe(by_id["etf_dividend_ucits_v1"])
    assert not is_validation_universe(by_id["etf_growth_ucits_v1"])


def test_lenses_suggest_the_ucits_universes_too():
    from aristos_council.strategy.rank_loader import load_rank_strategy
    div = load_rank_strategy(STRAT_DIR / "etf_dividend_v1.yaml")
    grw = load_rank_strategy(STRAT_DIR / "etf_growth_v1.yaml")
    assert "etf_dividend_ucits_v1" in div.suggested_universes
    assert "etf_growth_ucits_v1" in grw.suggested_universes
    # every suggested id still resolves to a real manifest
    for uid in div.suggested_universes + grw.suggested_universes:
        assert load_universe_by_id(uid, UNIV_DIR).id == uid
