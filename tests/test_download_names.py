"""Download filename scheme (ITEM 6).

Names carry the strategy id, the run MODE, and a run-start timestamp (Europe/Berlin), so
downloads no longer collide across runs/modes. Pure — tested without Streamlit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aristos_council.download_names import (
    company_check_download_name, mode_tag, universe_download_name)

# 2026-07-09 15:30 UTC -> 17:30 Europe/Berlin (CEST, UTC+2 in July).
_DT = datetime(2026, 7, 9, 15, 30, tzinfo=timezone.utc)
_STAMP = "2026-07-09_1730"


def test_mode_tag_maps_executed_modes():
    assert mode_tag("ranker-only") == "ranker"
    assert mode_tag("narrator") == "narrator"
    assert mode_tag("second_opinion") == "council"


def test_universe_filename_has_mode_and_parseable_timestamp():
    name = universe_download_name("magic_formula_momentum_v1", "narrator", _DT)
    assert name == f"universe_magic_formula_momentum_v1_narrator_{_STAMP}.md"
    assert "narrator" in name                              # mode present
    stamp = name.rsplit("_", 2)[-2] + "_" + name.rsplit("_", 1)[-1].removesuffix(".md")
    datetime.strptime(stamp, "%Y-%m-%d_%H%M")             # parseable timestamp


def test_ranker_only_universe_filename_tags_ranker():
    name = universe_download_name("conservative_plus_v1", "ranker-only", _DT)
    assert name == f"universe_conservative_plus_v1_ranker_{_STAMP}.md"


def test_company_check_filename_has_ticker_strategy_and_timestamp():
    name = company_check_download_name("MU", "magic_formula_momentum_v1", _DT)
    assert name == f"company_check_MU_magic_formula_momentum_v1_{_STAMP}.txt"
    stamp = name.removesuffix(".txt").rsplit("_", 2)
    datetime.strptime(stamp[-2] + "_" + stamp[-1], "%Y-%m-%d_%H%M")


def test_naive_run_start_is_treated_as_utc():
    naive = datetime(2026, 7, 9, 15, 30)                 # no tzinfo -> UTC
    assert company_check_download_name("MU", "s_v1", naive).endswith(f"{_STAMP}.txt")
