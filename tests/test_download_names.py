"""Download filename scheme (ITEM 6).

Names carry the strategy id, the run MODE, and a run-start timestamp (Europe/Berlin), so
downloads no longer collide across runs/modes. Pure — tested without Streamlit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aristos_council.download_names import (
    company_check_download_name, company_check_html_download_name, mode_tag,
    scoreboard_snapshots_download_name, slugify, universe_download_name,
    universe_html_download_name)

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


# --------------------------------------------------------------------------- #
# HTML export (REPORT-HTML-1) — the SAME scheme and stamp, .html extension.
# --------------------------------------------------------------------------- #
def test_universe_html_name_matches_the_md_name_but_for_the_extension():
    md = universe_download_name("etf_core_v1", "narrator", _DT)
    htm = universe_html_download_name("etf_core_v1", "narrator", _DT)
    assert htm == f"universe_etf_core_v1_narrator_{_STAMP}.html"
    assert htm == md.removesuffix(".md") + ".html"        # same stamp, same order


def test_universe_html_name_carries_no_ticker_it_is_a_cohort_file():
    htm = universe_html_download_name("etf_core_v1", "ranker-only", _DT)
    assert htm == f"universe_etf_core_v1_ranker_{_STAMP}.html"
    # A universe report belongs to no single name — no ticker may appear in its name.
    assert htm.startswith("universe_etf_core_v1_")
    assert "SXR8" not in htm and "MU" not in htm


def test_company_check_html_name_always_carries_the_ticker():
    txt = company_check_download_name("MU", "magic_formula_momentum_v1", _DT)
    htm = company_check_html_download_name("MU", "magic_formula_momentum_v1", _DT)
    assert htm == f"company_check_MU_magic_formula_momentum_v1_{_STAMP}.html"
    assert htm == txt.removesuffix(".txt") + ".html"
    assert "_MU_" in htm                                  # single-name file: ticker REQUIRED


def test_html_export_never_changes_the_canonical_names():
    # The .md/.txt names are the frozen record keys — the new ext argument defaults must
    # keep them byte-identical.
    assert universe_download_name("s_v1", "narrator", _DT) == \
        f"universe_s_v1_narrator_{_STAMP}.md"
    assert company_check_download_name("MU", "s_v1", _DT) == \
        f"company_check_MU_s_v1_{_STAMP}.txt"


# --------------------------------------------------------------------------- #
# UI-FIX-1 — slug of the universe display name in the run filename
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("Dividend ETFs (US)", "dividend-etfs-us"),          # parentheses
    ("Growth 40", "growth-40"),                          # spaces
    ("Käse Fund", "kase-fund"),                           # umlaut folds to ASCII
    ("ETF Index Tracker — UCITS", "etf-index-tracker-ucits"),  # em dash
    ("  leading/trailing  ", "leading-trailing"),         # stray whitespace + slash
    ("", ""),                                             # blank -> blank (omit, never invent)
])
def test_slugify_table(raw, expected):
    assert slugify(raw) == expected


def test_universe_filename_carries_display_name_slug_when_given():
    name = universe_download_name("magic_formula_v1", "narrator", _DT,
                                  universe_display_name="Dividend ETFs (US)")
    assert name == f"universe_dividend-etfs-us_magic_formula_v1_narrator_{_STAMP}.md"


def test_universe_filename_omits_slug_segment_when_display_name_blank():
    # Ad-hoc runs (Custom paste, or an unnamed Editor "Run once") carry no manifest
    # display_name — the pre-UI-FIX-1 shape is preserved exactly (never invent a name).
    name = universe_download_name("magic_formula_v1", "narrator", _DT,
                                  universe_display_name="")
    assert name == f"universe_magic_formula_v1_narrator_{_STAMP}.md"
    assert universe_download_name("magic_formula_v1", "narrator", _DT) == name


def test_universe_html_name_carries_the_same_slug_as_the_md_name():
    md = universe_download_name("s_v1", "narrator", _DT, universe_display_name="Growth 40")
    htm = universe_html_download_name("s_v1", "narrator", _DT,
                                      universe_display_name="Growth 40")
    assert htm == md.removesuffix(".md") + ".html"
    assert htm == f"universe_growth-40_s_v1_narrator_{_STAMP}.html"


def test_company_check_names_unchanged_by_ui_fix_1():
    # Company Check is a single-name file (ticker already identifies it) — UI-FIX-1
    # extends only the universe run's filename scheme, never this one.
    assert company_check_download_name("MU", "s_v1", _DT) == \
        f"company_check_MU_s_v1_{_STAMP}.txt"
    assert company_check_html_download_name("MU", "s_v1", _DT) == \
        f"company_check_MU_s_v1_{_STAMP}.html"


# --------------------------------------------------------------------------- #
# UI-FIX-1 — the scoreboard snapshot CSV export name
# --------------------------------------------------------------------------- #
def test_scoreboard_snapshots_download_name_is_stamped_and_extensioned():
    name = scoreboard_snapshots_download_name(_DT)
    assert name == f"scoreboard_snapshots_{_STAMP}.csv"


def test_scoreboard_snapshots_download_name_naive_datetime_treated_as_utc():
    naive = datetime(2026, 7, 9, 15, 30)
    assert scoreboard_snapshots_download_name(naive) == f"scoreboard_snapshots_{_STAMP}.csv"
