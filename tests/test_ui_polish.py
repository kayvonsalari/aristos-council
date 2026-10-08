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
    assert "IBM Plex Sans" in dark and "Cinzel" not in dark
    # B22-U1: no monospace face at all; numbers are Plex Sans with tabular figures
    assert "IBM Plex Mono" not in dark and "monospace" not in dark and "tabular-nums" in dark


# --------------------------------------------------------------------------- #
# item 2 - LENS-DESC-TOGGLE
# --------------------------------------------------------------------------- #
def _toggle(at, label):
    return next(t for t in at.toggle if t.label == label)


def _lens_boxes(at):
    return [c for c in at.checkbox if c.key and c.key.startswith("opt_lens_company_")]


def _lens_captions(at):
    return [str(c.value).replace("\$", "$") for c in at.caption]      # undo the $-escape


def test_the_display_heading_holds_both_toggles_and_the_old_name_is_gone():
    at = _app_test()
    labels = [t.label for t in at.toggle]
    assert "Show lens descriptions" in labels and "Show validation tools" in labels
    assert "Show validation & legacy tools" not in labels
    assert any("**Display**" in str(m.value) for m in at.sidebar.markdown)
    assert _toggle(at, "Show lens descriptions").value is False       # off by default


def test_off_shows_names_only_with_the_summary_as_the_tooltip_and_on_restores_it():
    import app
    at = _app_test()
    boxes = _lens_boxes(at)
    assert boxes and all(b.help for b in boxes), "every lens keeps its one-line summary as help"
    first_asks = boxes[0].help
    assert first_asks not in _lens_captions(at)                        # not printed under the box
    _toggle(at, "Show lens descriptions").set_value(True).run()
    assert not at.exception
    boxes = _lens_boxes(at)
    assert all(not b.help for b in boxes)
    assert first_asks in _lens_captions(at)                            # the full text is back
    # and the choice is remembered for the session: another rerun keeps it
    at.run()
    assert _toggle(at, "Show lens descriptions").value is True
    assert app.SHOW_LENS_DESC_KEY == "show_lens_desc"


def test_the_toggle_behaves_the_same_in_cohort_list_mode():
    at = _app_test()
    next(r for r in at.radio if r.label == "Input").set_value("Cohort / list").run()
    assert not at.exception
    boxes = [c for c in at.checkbox if c.key and c.key.startswith("opt_lens_list_")]
    assert boxes and all(b.help for b in boxes)
    _toggle(at, "Show lens descriptions").set_value(True).run()
    boxes = [c for c in at.checkbox if c.key and c.key.startswith("opt_lens_list_")]
    assert all(not b.help for b in boxes)


# --------------------------------------------------------------------------- #
# item 3 - the top of the page
# --------------------------------------------------------------------------- #
def test_the_top_of_the_page_has_the_one_short_line_and_no_machinery_lines():
    at = _app_test()
    text = _everything_on_screen(at)
    assert ("Check one company against its rivals, or rank a list. "
            "Every lens is one equal vote.") in text
    assert "Verdict: deterministic ranker" not in text
    assert "Narrative: LLM (non-judging)" not in text
    assert "Screen → rank → gates" not in text and "only narrates" not in text


def test_deploy_and_the_developer_menu_are_hidden_by_the_toolbar_mode():
    import tomllib
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["client"]["toolbarMode"] in ("minimal", "viewer")
    assert cfg["theme"]["base"] == "dark" and cfg["theme"]["primaryColor"] == "#7FB2FF"
    assert cfg["theme"]["backgroundColor"] == "#0E1217" and cfg["theme"]["textColor"] == "#E6EAF0"
    assert cfg["theme"]["secondaryBackgroundColor"] == "#161D26"


# --------------------------------------------------------------------------- #
# items 4 and 5 - the company page (cards, chips, table) and the hidden workshop details
# --------------------------------------------------------------------------- #
def _company_report(tmp_path, lenses=None):
    from tests.test_company_report import RAW, SCREENED, _run
    return _run(lenses or [RAW, SCREENED], tmp_path=tmp_path)


def _render(report, *, validation: bool = False):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    script = (
        "import sys, streamlit as st\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "import app\n"
        "from aristos_council.ui_text import install\n"
        "install(st)\n"
        "app._render_company_report(st.session_state['rep'])\n")
    at = AppTest.from_string(script, default_timeout=120)
    at.session_state["rep"] = report
    if validation:
        at.session_state["show_legacy"] = True
    at.run()
    assert not at.exception, at.exception
    return at


def _html_blob(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def test_the_company_page_has_header_chips_chip_table_and_four_cards(tmp_path):
    at = _render(_company_report(tmp_path))
    html = _html_blob(at)
    assert 'class="ar-card ar-header"' in html and "ar-name" in html
    assert "compared with" in html and "similar" in html
    assert "ar-chiprow" in html
    assert 'class="ar-table"' in html and "Vote or mark" in html
    for title in ("Valuation vs own history", "Price", "Earnings price", "Balance sheet"):
        assert f'<div class="ar-stat-title">{title}</div>' in html
    assert html.count('class="ar-card"') == 4


def test_chips_show_the_word_and_the_counts_match_the_agreement(tmp_path):
    from aristos_council.company_cards import lens_rows, summary_chips
    report = _company_report(tmp_path)
    chips = summary_chips(report)
    ag = report.agreement
    for key, word in (("buy", "BUY"), ("hold", "HOLD"), ("sell", "SELL")):
        n = len(getattr(ag, key))
        assert (f">{n} {word}<" in chips) is bool(n)          # only counts above zero
    for row in lens_rows(report):
        assert row["word"], "a vote is never shown by colour alone"


def test_the_lens_table_reads_the_same_cells_as_the_story_table(tmp_path):
    from aristos_council.company_cards import lens_rows
    from aristos_council.company_story import table_rows
    report = _company_report(tmp_path)
    rows, story = lens_rows(report), table_rows(report)
    assert [r["lens"] for r in rows] == [r.lens for r in story]
    assert [r["reason"] for r in rows] == [r.reason for r in story]
    for r, v in zip(rows, report.votes):
        if v.ranked:
            assert r["word"] == v.word and v.result().startswith(r["word"])


def test_a_card_abstains_with_a_reason_when_its_figure_is_missing(tmp_path):
    from aristos_council import company_cards as cc
    report = _company_report(tmp_path)
    report.check.price_and_cash = None
    report.check.band_percentile = None
    report.check.valuation_band = "not evaluated — usable data covers only 1.8 years"
    html = cc.stat_cards(report)
    assert "usable data covers only 1.8 years" in html
    assert "no price history" in html and "no analyst estimate" in html
    assert html.count("ar-abstain") >= 3                       # three cards abstain; none shows a 0


def test_a_bank_balance_sheet_card_says_not_meaningful_for_banks(tmp_path):
    from aristos_council import company_cards as cc
    from aristos_council.abs_readings import DebtAndCash, Reading
    report = _company_report(tmp_path)
    report.check.debt_and_cash = DebtAndCash(net_debt=Reading(not_meaningful="not meaningful for banks and insurers"))
    assert "not meaningful for banks" in cc.balance_sheet_card(report)


def test_band_wording_follows_the_percentile():
    from aristos_council.company_cards import band_word
    assert band_word(12) == "Cheap vs its own 5 years" and band_word(94) == "Dear vs its own 5 years"
    assert band_word(50) == "Mid-range vs its own 5 years" and band_word(None) == "Valuation band not read"


def test_workshop_details_are_hidden_unless_validation_tools_is_on(tmp_path):
    report = _company_report(tmp_path)
    report.saved_to = str(tmp_path / "runs" / "x")
    off = "\n".join(str(c.value) for c in _render(report).caption)
    assert "Ran in" not in off and "day-cache" not in off and "saved under" not in off
    on = "\n".join(str(c.value) for c in _render(report, validation=True).caption)
    assert "Ran in" in on and "saved under" in on


def test_how_lenses_are_graded_lives_inside_sources_and_downloads_stay(tmp_path):
    at = _render(_company_report(tmp_path))
    assert not any(str(c.value).strip() == "How lenses are graded: docs/BACKTEST.md" for c in at.caption)
    assert any("How lenses are graded:" in str(m.value) and "docs/BACKTEST.md" in str(m.value)
               for m in at.markdown)
    assert len(at.get("download_button")) == 3


# --------------------------------------------------------------------------- #
# item 6 - the start state
# --------------------------------------------------------------------------- #
def test_the_start_state_is_a_quiet_panel_with_three_steps_and_a_hint():
    at = _app_test()
    html = _html_blob(at)
    assert "Check one company against its rivals" in html
    for step in ("1. Find a company", "2. Tick lenses", "3. Run, free"):
        assert f'<span class="ar-step">{step}</span>' in html
    assert "Want to rank a whole list? Switch to Cohort / list." in html
    assert "class=\"ar-big" not in html and "class=\"ar-table\"" not in html   # no result chrome before a run


def test_the_find_a_company_placeholder_gives_an_example():
    at = _app_test()
    find = next(t for t in at.text_input if t.label == "Find a company")
    assert find.placeholder == "Name or ticker, e.g. Novo Nordisk or NVO"


def test_the_start_panel_goes_once_a_result_is_there(tmp_path):
    at = _render(_company_report(tmp_path))
    assert "ar-empty" not in _html_blob(at)


# --------------------------------------------------------------------------- #
# item 7 - list results in the same style
# --------------------------------------------------------------------------- #
def test_grid_cells_become_a_position_and_a_verdict_chip_and_other_cells_stay_as_written():
    from aristos_council.list_cards import verdict_cell
    assert 'ar-chip-buy">BUY<' in verdict_cell("#1 of 9 · BUY") and "#1 of 9" in verdict_cell("#1 of 9 · BUY")
    assert 'ar-chip-hold">HOLD<' in verdict_cell("#4 of 9 · HOLD · ranked on 2 of 3 factors")
    assert "ranked on 2 of 3 factors" in verdict_cell("#4 of 9 · HOLD · ranked on 2 of 3 factors")
    assert 'ar-chip-na">doubted<' in verdict_cell("#2 of 9 · doubted")
    for text in ("excluded — no operating profit", "no data", "fetch failed (rerun)", "—"):
        out = verdict_cell(text)
        assert "ar-chip" not in out and text.replace("—", "—") in out.replace("&#36;", "$")


def test_the_agreement_table_marks_buy_and_sell_votes_with_chips_and_keeps_every_cell():
    from aristos_council.list_cards import agreement_table
    cols = ["Name", "BUY votes", "SELL votes", "Forensic", "Valuation percentile", "Marks"]
    rows = [{"Name": "Acme (ACM)", "BUY votes": "2 of 3: Quality, Growth (1 did not apply)",
             "SELL votes": "—", "Forensic": "no concern", "Valuation percentile": "94th",
             "Marks": "priced high · one view"}]
    html = agreement_table(cols, rows)
    assert 'ar-chip-buy">BUY<' in html and "2 of 3: Quality, Growth (1 did not apply)" in html
    assert "ar-chip-sell" not in html                               # "—" is no vote
    assert 'ar-chip-na">no concern<' in html and "94th" in html
    assert html.count('class="ar-badge"') == 2 and "Acme (ACM)" in html


def test_a_list_result_renders_the_chip_grid_and_the_shortlist(tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from tests.test_merged_multi_report import MOMENTUM, _multi
    from tests.test_multi_strategy_run import RAW, SCREENED
    script = ("import sys, streamlit as st\n" f"sys.path.insert(0, r'{ROOT}')\n"
              "import app\nfrom aristos_council.ui_text import install\ninstall(st)\n"
              "app._render_multi_strategy_result(st.session_state['res'])\n")
    at = AppTest.from_string(script, default_timeout=120)
    at.session_state["res"] = _multi([SCREENED, RAW, MOMENTUM])
    at.run()
    assert not at.exception, at.exception
    html = _html_blob(at)
    assert 'class="ar-table"' in html and "ar-chip-buy" in html or "ar-chip-hold" in html
    assert "Name" in html
    assert len(at.get("download_button")) == 2                      # downloads stay


def test_the_theme_follows_the_configured_base_not_the_browser(monkeypatch):
    """A browser preferring light made st.context.theme report "light" over our dark config (white cards
    on a dark page, seen on 2026-10-07). The configured base wins."""
    import streamlit as st
    from aristos_council import ui_style
    monkeypatch.setattr(st, "get_option", lambda key: "dark" if key == "theme.base" else None)
    assert ui_style.theme_name() == "dark"
    monkeypatch.setattr(st, "get_option", lambda key: "light" if key == "theme.base" else None)
    assert ui_style.theme_name() == "light"


def test_the_light_logo_has_a_dark_wordmark():
    svg = (ASSETS / "aristos_logo_light.svg").read_text(encoding="utf-8")
    assert 'fill="#16202C"' in svg and 'fill="#E6EAF0"' not in svg and "<text" not in svg


# --------------------------------------------------------------------------- #
# Batch 22
# --------------------------------------------------------------------------- #
def test_u3_the_paid_extras_sit_below_the_lens_grid_under_their_own_heading():
    at = _app_test()
    html = _html_blob(at)
    assert "Optional extras (paid AI calls)" in html
    labels = [c.label for c in at.checkbox]
    extras = [i for i, l in enumerate(labels) if l in ("Plain-English summary", "Council opinion")]
    lenses = [i for i, c in enumerate(at.checkbox) if c.key and c.key.startswith("opt_lens_company_")]
    assert extras and lenses and min(extras) > max(lenses)
    assert all(c.help for c in at.checkbox if c.label in ("Plain-English summary", "Council opinion"))


def test_u4_the_free_run_buttons_say_free_in_one_short_phrase():
    import app
    assert app.run_button_label(with_council=False, n_strategies=3) == "▶ Run company check · free"
    assert app.run_button_label(app.RUN_MODE_RANKER, n_strategies=7) == "▶ Run 7 lenses · free"
    assert app.run_button_label(app.RUN_MODE_RANKER, n_strategies=1) == "▶ Run · free"
    assert "model call" in app.run_button_label(with_council=False, with_reader=True, n_strategies=1)
    at = _app_test()
    assert any(b.label == "▶ Run company check · free" for b in at.button)


def test_u5_the_download_buttons_are_short_and_carry_the_file_name_as_tooltip(tmp_path):
    at = _render(_company_report(tmp_path))
    buttons = at.get("download_button")
    assert [b.proto.label for b in buttons] == ["⬇ Download text", "⬇ Download HTML", "⬇ Download Markdown"]
    helps = [b.proto.help for b in buttons]
    assert helps[0].endswith(".txt") and helps[1].endswith(".html") and helps[2].endswith(".md")
    assert all("company_check_" in h for h in helps)


def test_u6_the_valuation_bar_is_blue_grey_orange_not_green_amber_red():
    from aristos_council.ui_style import DARK, LIGHT, css, percentile_bar
    assert (DARK["bar_a"], DARK["bar_b"], DARK["bar_c"]) == ("#2E5C8F", "#263241", "#8F5A2E")
    for pal in (DARK, LIGHT):
        for key in ("bar_a", "bar_b", "bar_c"):
            assert pal[key] not in (DARK["buy_fg"], DARK["hold_fg"], DARK["sell_fg"], "#3FB68B", "#E6B246", "#E5484D")
    assert "#2E5C8F" in css("dark") and "#8F5A2E" in css("dark")
    assert "left:30.0%" in percentile_bar(30)


def test_u7_the_heading_link_icons_are_hidden():
    from aristos_council.ui_style import css
    assert 'stHeaderActionElements"] { display: none !important; }' in css("dark")


def test_u9_the_css_lifts_the_main_column_level_with_the_logo_row():
    from aristos_council.ui_style import css
    c = css("dark")
    assert 'stMainBlockContainer"] { padding-top: .75rem !important; }' in c
    assert 'stHeader"] { background: transparent !important; height: 0 !important' in c
    assert "stElementContainer" in c and "style) { display: none; }" in c
    assert "max-width: 640px" in c and "padding-top: 3rem" in c        # phone: room for the sidebar toggle


def test_b11_the_finance_arm_note_is_a_muted_caption_on_the_page(tmp_path):
    from aristos_council.abs_readings import debt_and_cash
    from tests.test_abs_readings import _f
    report = _company_report(tmp_path)
    report.check.debt_and_cash = debt_and_cash(_f(ticker="F", total_debt=146.2e9, total_cash=5e9,
                                                  free_cash_flow=12.5e9))
    at = _render(report)
    assert any("Includes debt of its car-loan arm" in str(c.value) for c in at.caption)


# --------------------------------------------------------------------------- #
# Batch 23
# --------------------------------------------------------------------------- #
def test_b23_n8_the_balance_sheet_card_adds_the_runway_only_for_a_genuine_burn(tmp_path):
    from aristos_council import company_cards as cc
    from aristos_council.abs_readings import debt_and_cash
    from tests.test_abs_readings import _viking
    report = _company_report(tmp_path)
    report.check.debt_and_cash = debt_and_cash(_viking())
    card = cc.balance_sheet_card(report)
    assert "net cash &#36;497.6m" in card and "lasts about 1.8 years at FY2025&#x27;s spending" in card
    assert "interest cover" in card                                    # the first line is still there
    byd = debt_and_cash(_viking(total_debt=1.0e9, total_cash=3.8e9, free_cash_flow=-97.7e9,
                                operating_cash_flow=59.1e9, aligned_annual={"operating_cash_flow": [59.1e9]}))
    report.check.debt_and_cash = byd
    assert "lasts" not in cc.balance_sheet_card(report)


def test_b23_n2_no_card_ever_shows_text_cut_off_mid_sentence(tmp_path):
    """VKTX's valuation card read "...(44 with no positive operating profit, the" - cut at 90 characters."""
    import re

    from aristos_council import company_cards as cc
    from aristos_council.abs_readings import DebtAndCash, Reading
    report = _company_report(tmp_path)
    vktx = ("not evaluated \u2014 valuation measure undefined in 61 of 61 months (44 with no positive "
            "operating profit, the rest lack usable statements)")
    ford = ("not evaluated \u2014 valuation measure undefined in 38 of 61 months (21 with no positive operating "
            "profit, the rest lack usable statements); the usable months span 1.8y and the band needs 3.0y")
    report.check.band_percentile = None
    report.check.valuation_band = vktx
    assert "Not read: no operating profit in most months" in cc.valuation_card(report)
    report.check.valuation_band = ford
    assert "Not read: only 1.8 years of usable history (needs 3.0)" in cc.valuation_card(report)
    report.check.valuation_band = "not evaluated \u2014 " + "something rather long and wordy " * 8
    assert "Not read: not enough usable history" in cc.valuation_card(report)
    # every other card: a long reason falls back to a short whole one
    long_note = "the reported free cash flow is larger than operating cash flow, so the two disagree and neither is used"
    report.check.debt_and_cash = DebtAndCash(net_debt=Reading(note=long_note))
    report.check.price_and_cash = None
    for html in (cc.balance_sheet_card(report), cc.price_card(report), cc.earnings_price_card(report),
                 cc.valuation_card(report)):
        for text in re.findall(r'class="ar-abstain">(.*?)</div>', html):
            assert len(text) <= cc.SHORT_REASON_MAX + 40 and not text.endswith((",", " the", " and", " of"))
    assert "not reported" in cc.balance_sheet_card(report)


def _list_page(validation: bool):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from tests.test_merged_multi_report import _multi
    from tests.test_multi_strategy_run import RAW, SCREENED
    script = ("import sys, streamlit as st\n" f"sys.path.insert(0, r'{ROOT}')\n"
              "import app\nfrom aristos_council.ui_text import install\ninstall(st)\n"
              "app._render_multi_strategy_result(st.session_state['res'])\n")
    at = AppTest.from_string(script, default_timeout=120)
    at.session_state["res"] = _multi([SCREENED, RAW])
    if validation:
        at.session_state["show_legacy"] = True
    at.run()
    assert not at.exception, at.exception
    return "\n".join(str(getattr(e, "value", "")) for k in ("caption", "markdown", "info", "warning", "success")
                     for e in at.get(k))


def test_b23_n6_the_list_page_hides_the_machinery_header_unless_validation_tools_is_on():
    off, on = _list_page(False), _list_page(True)
    for jargon in ("Verdict: deterministic ranker", "Narrative: none", "no LLM ran", "Multi-lens re-grade"):
        assert jargon not in off, jargon
    assert "Verdict: deterministic ranker" in on and "no LLM ran" in on
    assert "Shortlist" in off or "ranked by at least one" in off      # the real content is still there


def test_b23_n7_the_list_page_downloads_are_short_and_carry_the_file_names(tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from tests.test_merged_multi_report import _multi
    from tests.test_multi_strategy_run import RAW, SCREENED
    script = ("import sys, streamlit as st\n" f"sys.path.insert(0, r'{ROOT}')\n"
              "import app\nfrom aristos_council.ui_text import install\ninstall(st)\n"
              "app._render_multi_strategy_result(st.session_state['res'])\n")
    at = AppTest.from_string(script, default_timeout=120)
    at.session_state["res"] = _multi([SCREENED, RAW])
    at.run()
    assert not at.exception, at.exception
    buttons = at.get("download_button")
    assert [b.proto.label for b in buttons] == ["⬇ Download Markdown", "⬇ Download HTML"]
    assert buttons[0].proto.help.endswith(".md") and buttons[1].proto.help.endswith(".html")
