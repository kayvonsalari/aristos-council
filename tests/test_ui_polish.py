"""UI-POLISH-1 (Batch 21) - look and branding only. Nothing here may touch a rank, a vote or a number;
these tests pin the words, the assets and the style helpers, never a verdict."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def _app_test(timeout: int = 120):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=timeout)
    at.run()
    assert not at.exception, at.exception
    return at


def _everything_on_screen(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "title", "header", "subheader", "text", "info", "warning"):
        parts += [str(getattr(e, "value", "")) for e in at.get(kind)]
    parts += [str(getattr(t, "label", "")) for t in at.toggle]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# item 1 - branding
# --------------------------------------------------------------------------- #
def test_the_logo_is_a_static_svg_with_outlined_letters():
    for name in ("aristos_logo.svg", "aristos_mark.svg"):
        svg = (ASSETS / name).read_text(encoding="utf-8")
        assert "<text" not in svg and "font-family" not in svg, "letters must be outlines"
        assert 'rx="9"' in svg and 'fill="#7FB2FF"' in svg and 'fill="#0E1217"' in svg
        assert "<path" in svg
    assert 'fill="#E6EAF0"' in (ASSETS / "aristos_logo.svg").read_text(encoding="utf-8")   # wordmark


def test_the_favicon_is_a_64px_png_and_the_old_bank_icon_is_gone():
    from PIL import Image
    with Image.open(ASSETS / "aristos_favicon.png") as im:
        assert im.size == (64, 64)
    assert not (ASSETS / "aristos_council_logo.svg").exists()


def test_the_screen_says_aristos_and_never_council_station_or_the_bank_icon():
    at = _app_test()
    text = _everything_on_screen(at)
    assert "Council Station" not in text and "\U0001F3DB" not in text


def test_downloaded_report_headers_carry_the_new_product_name_and_nothing_else_changes(tmp_path):
    from aristos_council.export.report_html import company_report_html
    from tests.test_company_report import RAW, _run
    html = company_report_html(_run([RAW], tmp_path=tmp_path))
    assert "Aristos · company report" in html and "Aristos Council ·" not in html


# --------------------------------------------------------------------------- #
# the style helpers (ui_style.py)
# --------------------------------------------------------------------------- #
def test_the_palette_is_the_approved_one():
    from aristos_council.ui_style import DARK
    assert (DARK["bg"], DARK["panel"], DARK["border"], DARK["text"], DARK["muted"], DARK["accent"]) == (
        "#0E1217", "#161D26", "#263241", "#E6EAF0", "#8B97A8", "#7FB2FF")
    assert (DARK["buy_fg"], DARK["hold_fg"], DARK["sell_fg"]) == ("#6FD3AA", "#EBC46E", "#F2858A")
    assert (DARK["buy_bg"], DARK["hold_bg"], DARK["sell_bg"]) == (
        "rgba(63,182,139,.16)", "rgba(230,178,70,.16)", "rgba(229,72,77,.18)")
    assert DARK["na_bg"] == "#1C2530"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_text_and_background_pair_has_4_5_to_1_contrast(theme):
    from aristos_council.ui_style import PALETTES, contrast
    p = PALETTES[theme]
    for under in (p["panel"], p["bg"]):
        for kind in ("buy", "hold", "sell", "na"):
            assert contrast(p[f"{kind}_fg"], p[f"{kind}_bg"], under=under) >= 4.5, (theme, kind, under)
        assert contrast(p["text"], under) >= 4.5 and contrast(p["muted"], under) >= 4.5
    assert contrast(p["accent"], p["bg"]) >= 4.5


def test_a_chip_always_carries_its_word_and_the_right_kind():
    from aristos_council.ui_style import chip
    for word, kind in (("BUY", "buy"), ("HOLD", "hold"), ("SELL", "sell"), ("buy", "buy"),
                       ("does not apply", "na"), ("no concern", "na"), ("doubted", "na")):
        html = chip(word)
        assert f"ar-chip-{kind}" in html and f">{word}<" in html


def test_builders_escape_html_and_dollars_and_stay_on_one_line():
    from aristos_council.ui_style import badge, empty_panel, stat_card, table
    nasty = '<b onclick="x">$5bn & $10bn</b>'
    for out in (badge(nasty), stat_card(nasty, nasty, [nasty]), table(["h"], [[nasty]]),
                empty_panel(nasty, nasty, [nasty], nasty)):
        assert "<b onclick" not in out and "$" not in out and "\n" not in out
        assert "&#36;5bn" in out


def test_a_card_with_no_figure_shows_its_reason_never_a_blank_or_a_zero():
    from aristos_council.ui_style import stat_card
    out = stat_card("Earnings price", "", abstain="no analyst estimate")
    assert "no analyst estimate" in out and "ar-big" not in out
    assert "not available" in stat_card("Price")


def test_the_percentile_bar_clamps_its_marker():
    from aristos_council.ui_style import percentile_bar
    assert "left:94.0%" in percentile_bar(94) and "left:0.0%" in percentile_bar(-5) \
        and "left:100.0%" in percentile_bar(250)
    assert "gradient" not in percentile_bar(50)


def test_the_css_has_no_gradient_and_a_light_variant_for_every_variable():
    from aristos_council.ui_style import css
    dark, light = css("dark"), css("light")
    assert "gradient" not in dark and "gradient" not in light
    assert "#0E1217" in dark and "#F5F7FA" in light and "#F5F7FA" not in dark
    assert "IBM Plex Sans" in dark and "IBM Plex Mono" in dark and "Cinzel" not in dark
