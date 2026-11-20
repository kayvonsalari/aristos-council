"""PAGE-LAYOUT-1: the "Analyse a company" page (pilot mode), layout and wording only.

Group 3 of the bug hunt of 2026-11-20, as the owner listed it:
  1. the logo was drawn 32px high with its top half above the window (fault 23);
  2. before a company is picked the page is one narrow centred column, and widens for a report;
  3. "Find a company" and "Ticker" are ONE box, with no example ticker shown as if it were a value;
  4. the run button never looks greyed out;
  5. the explanation panel sits above the search box and the button;
  6. the "Market index: peer sizes ... days old" line is not at the top (it is in the workings).

No figure and no verdict rule is touched: these tests are about what is drawn and in what order.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from aristos_council import pilot, ui_style

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 1. the logo
# --------------------------------------------------------------------------- #
def test_the_logo_is_the_whole_file_inline_in_the_page_column():
    svg = (ROOT / "assets" / "aristos_logo.svg").read_text(encoding="utf-8")
    html = ui_style.logo_html(svg)
    assert html.startswith('<div class="ar-logo"><img alt="Aristos" src="data:image/svg+xml;base64,')
    data = re.search(r"base64,([^\"]+)\"", html).group(1)
    assert base64.b64decode(data).decode("utf-8").strip() == svg.strip()     # the wordmark, not the "A" icon
    assert 'aria-label="Aristos"' in svg and 'width="142"' in svg           # mark + wordmark (the icon is 32 wide)


def test_no_logo_markup_when_there_is_no_image_text():
    assert ui_style.logo_html("") == "" and ui_style.logo_html("not an svg") == ""


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_logo_is_bigger_than_the_32px_it_was_and_fills_its_box(theme):
    css = ui_style.css(theme)
    assert ui_style.LOGO_HEIGHT_PX >= 44 and ui_style.LOGO_HEIGHT_PHONE_PX >= 36
    assert f".ar-logo img {{ height: {ui_style.LOGO_HEIGHT_PX}px;" in css
    # Streamlit's own "scale-down" drew it at the file's 32px inside the taller box, off the left edge
    rule = css[css.index(".ar-logo img {"):].split("}")[0]
    assert "object-fit: contain !important" in rule and "object-position: left center" in rule
    assert f".ar-logo img {{ height: {ui_style.LOGO_HEIGHT_PHONE_PX}px; }}" in css.split("@media (max-width: 640px)")[1]


# --------------------------------------------------------------------------- #
# 2. the narrow start page
# --------------------------------------------------------------------------- #
def test_the_start_page_is_one_centred_column_that_a_phone_never_reaches():
    css = ui_style.START_WIDTH_CSS
    assert '[data-testid="stMainBlockContainer"]' in css
    assert f"max-width: {ui_style.START_COLUMN_PX}px" in css                 # a cap, so a 390px phone is untouched
    assert "margin-left: auto" in css and "margin-right: auto" in css        # centred, not hard against the left
    assert 600 <= ui_style.START_COLUMN_PX <= 900
    assert ui_style.start_width_style() == f"<style>{css}</style>"


# --------------------------------------------------------------------------- #
# 4. the run button's label can be read on the button
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_run_buttons_label_contrasts_with_the_button(theme):
    """The dark theme drew a white label on the pale-blue accent: 2.2 to 1, which read as greyed out."""
    p = ui_style.PALETTES[theme]
    assert ui_style.contrast(p["on_accent"], p["accent"]) >= 4.5
    css = ui_style.css(theme)
    assert '[data-testid="stBaseButton-primary"]' in css
    assert f"color: {p['on_accent']} !important" in css and "background: var(--ar-accent) !important" in css


def test_white_on_the_dark_themes_accent_was_the_fault():
    assert ui_style.contrast("#FFFFFF", ui_style.DARK["accent"]) < 3.0


# --------------------------------------------------------------------------- #
# 5. the panel above the search box is the short form
# --------------------------------------------------------------------------- #
def test_the_top_panel_is_the_short_form_and_drops_an_empty_hint():
    top = ui_style.empty_panel("Title", "Sentence.", ["1. Find a company", "2. Run"], "", top=True)
    assert 'class="ar-card ar-empty ar-empty-top"' in top and "ar-hint" not in top
    assert "<h3>Title</h3>" in top and "1. Find a company" in top
    below = ui_style.empty_panel("Title", "Sentence.", ["1"], "a hint")
    assert 'class="ar-card ar-empty"' in below and '<div class="ar-hint">a hint</div>' in below
    assert ".ar-empty.ar-empty-top" in ui_style.css("dark")


# --------------------------------------------------------------------------- #
# the page itself (pilot mode on)
# --------------------------------------------------------------------------- #
def _pilot_page(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv(pilot.ENV, "1")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


PANEL_MARK = 'class="ar-card ar-empty ar-empty-top"'      # the panel itself, not the stylesheet that names it


def _markdown(at) -> list[str]:
    return [str(getattr(m, "value", "")) for m in at.markdown]


def test_the_start_page_has_one_company_box_and_no_example_ticker(monkeypatch):
    import app
    at = _pilot_page(monkeypatch)
    boxes = list(at.text_input)
    assert [str(b.label) for b in boxes] == ["Find a company"]               # not "Find a company" + "Ticker"
    assert [str(getattr(b, "key", "")) for b in boxes] == ["cc_ticker"]
    assert boxes[0].value == ""                                              # nothing pre-filled
    assert boxes[0].placeholder == app.COMPANY_BOX_HINT and "MU" not in boxes[0].placeholder
    assert not any(str(getattr(b, "key", "")) == "cc_find_use" for b in at.button)


def test_the_logo_the_panel_and_the_narrow_column_are_on_the_start_page(monkeypatch):
    import app
    at = _pilot_page(monkeypatch)
    md = _markdown(at)
    assert any('class="ar-logo"' in m for m in md)
    assert any(ui_style.START_WIDTH_CSS in m for m in md)
    panel = next(m for m in md if PANEL_MARK in m)
    assert app.PILOT_START_PANEL[0] in panel                                 # "What the price requires, and ..."


def test_the_explanation_is_drawn_above_the_search_box(monkeypatch):
    """It used to be drawn after the run button: below the fold on a phone."""
    at = _pilot_page(monkeypatch)
    order = []
    for el in at.main:
        text = str(getattr(el, "value", "") or "")
        if PANEL_MARK in text:
            order.append("panel")
        if getattr(el, "type", "") == "text_input" or "Find a company" in text:
            order.append("search")
        if str(getattr(el, "key", "")) == "cc_run":
            order.append("button")
    assert order and order[0] == "panel", order
    assert order.index("panel") < order.index("search") < order.index("button"), order
    assert order.count("panel") == 1                                         # not drawn a second time below


def test_the_market_index_line_is_not_at_the_top_of_the_company_page(monkeypatch):
    import aristos_council.market_index as mi
    monkeypatch.setattr(mi, "index_age_notice", lambda *_a, **_k: "peer sizes from 2026-10-15; 36 days old")
    at = _pilot_page(monkeypatch)
    assert not any("Market index:" in str(getattr(c, "value", "")) for c in at.caption)


def test_the_market_index_line_stays_on_the_list_page_which_has_no_workings(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    import aristos_council.market_index as mi
    monkeypatch.setattr(mi, "index_age_notice", lambda *_a, **_k: "peer sizes from 2026-10-15; 36 days old")
    monkeypatch.setenv(pilot.ENV, "0")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    assert any("Market index: peer sizes from 2026-10-15; 36 days old." in str(getattr(c, "value", ""))
               for c in at.caption)


def test_the_run_button_is_never_greyed_out_and_an_empty_box_says_what_to_do(monkeypatch):
    import app
    import aristos_council.company_report as cr
    called = []
    monkeypatch.setattr(cr, "run_company_report", lambda *a, **k: called.append(a) or None)
    at = _pilot_page(monkeypatch)
    button = at.button(key="cc_run")
    assert button.disabled is False                                          # it was disabled with an empty box
    button.click().run()
    assert not at.exception
    assert [str(w.value) for w in at.warning] == [app.NO_COMPANY_YET]
    assert called == []                                                      # nothing was run
    assert any(PANEL_MARK in m for m in _markdown(at))                       # still the start page


# --------------------------------------------------------------------------- #
# 3. what the one box resolves to
# --------------------------------------------------------------------------- #
def test_each_typed_text_has_its_own_match_list():
    """With one shared key the list kept showing the previous company after the text changed."""
    import app
    assert app.match_list_key("RVMD") != app.match_list_key("FEVR.L")
    assert app.match_list_key(" Rvmd ") == app.match_list_key("rvmd")
    assert app.match_list_key("x").startswith("cc_find_pick")


def test_the_match_line_reads_as_it_did_in_the_old_search_box():
    import app
    from aristos_council.company_search import CompanyMatch
    m = CompanyMatch(ticker="SIE.XETRA", name="Siemens Aktiengesellschaft", exchange="XETRA", market="XETRA",
                     country="Germany", market_cap_usd=236.4e9, is_home=True,
                     cohorts=("Industrials - Industrial Machinery",))
    assert app.match_label(m, cohorts_known=True) == ("Siemens Aktiengesellschaft (SIE.XETRA) — Germany — Xetra "
                                                      "— $236.4bn — Industrials - Industrial Machinery")
    none = CompanyMatch(ticker="X.US", name="X Corp", exchange="NYSE", market="US", country="US",
                        market_cap_usd=1e9, is_home=True, cohorts=())
    assert app.match_label(none, cohorts_known=True).endswith("— no cohort")
    assert not app.match_label(none, cohorts_known=False).endswith("— no cohort")
