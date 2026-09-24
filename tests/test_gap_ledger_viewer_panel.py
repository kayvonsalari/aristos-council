"""GAP-VIEWER-1 items 4 and 5 — the left panel, and the other tabs reading alike.

Item 4 is three sections: the **day** (picker, newest first, plus that day's counts), the
**filters**, and **how to read it** with the commands collapsed. Plus the Deploy button hidden —
and *only* that, because ``app.py`` records in writing what happened the last time a chrome-strip
was aggressive: it took out the toolbar menu and the sidebar collapse toggle with it.

Item 5 is that the other tabs use the same columns and the same colours, and that the scorecard
says how far off an answer it is as a number rather than only as a refusal.

The filter logic lives in ``gap_ledger.viewer`` as pure functions, so most of this needs no
browser; the few genuinely app-level properties are driven through ``AppTest``.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from aristos_council.gap_ledger.ledger import (GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow,
                                               write_day)
from aristos_council.gap_ledger.score import score
from aristos_council.gap_ledger.verify import SOURCE_IBKR, SOURCE_YFINANCE
from aristos_council.gap_ledger.viewer import (CARRIED_ON, COLUMNS, FLAT, HOW_TO_READ,
                                               NEWS_ANY, NEWS_CHOICES, NEWS_WITH,
                                               NEWS_WITHOUT, RESULT_ANY, RESULT_CHOICES,
                                               RESULT_PENDING, REVERSED, UNVERIFIED_ONLY,
                                               VERIFIED_ANY, VERIFIED_CHOICES,
                                               VERIFIED_ONLY, apply_filters,
                                               checkpoint_markdown, filter_caption,
                                               gap_cell, points_cell, scorecard_progress,
                                               table_markdown)

ROOT = Path(__file__).resolve().parents[1]
_APP = ROOT / "gap_ledger_app.py"
DAY = date(2026, 9, 22)


def _row(ticker="AAA", **kwargs) -> LedgerRow:
    base = dict(date=DAY.isoformat(), ticker=ticker, group=GROUP_CANDIDATE, gap_pct=0.10,
                source=SOURCE_IBKR, relative_volume=39.0, news_found="news found",
                open_price=100.0, close_price=110.0)
    base.update(kwargs)
    return LedgerRow(**base)


# --------------------------------------------------------------------------- #
# item 4(b) — the filters
# --------------------------------------------------------------------------- #
def test_the_filters_default_to_hiding_nothing():
    rows = [_row("A"), _row("B", source=SOURCE_YFINANCE, news_found="no news found")]
    assert apply_filters(rows) == rows
    assert apply_filters(rows, verified=VERIFIED_ANY, news=NEWS_ANY,
                         result=RESULT_ANY) == rows


def test_the_verified_filter_works_both_ways():
    rows = [_row("IB"), _row("YF", source=SOURCE_YFINANCE)]
    assert [r.ticker for r in apply_filters(rows, verified=VERIFIED_ONLY)] == ["IB"]
    assert [r.ticker for r in apply_filters(rows, verified=UNVERIFIED_ONLY)] == ["YF"]


def test_an_old_csv_row_counts_as_unverified_in_the_filter():
    """No ``source`` column at all, which is true of every pre-IBKR file."""
    rows = [_row("OLD", source="")]
    assert [r.ticker for r in apply_filters(rows, verified=UNVERIFIED_ONLY)] == ["OLD"]
    assert apply_filters(rows, verified=VERIFIED_ONLY) == []


def test_the_news_filter_works_both_ways():
    rows = [_row("HAS"), _row("NONE", news_found="no news found"),
            _row("REL", news_found="related, not matched")]
    assert [r.ticker for r in apply_filters(rows, news=NEWS_WITH)] == ["HAS"]
    assert [r.ticker for r in apply_filters(rows, news=NEWS_WITHOUT)] == ["NONE", "REL"]


@pytest.mark.parametrize("wanted, ticker", [(CARRIED_ON, "UP"), (REVERSED, "DOWN"),
                                            (FLAT, "SAME")])
def test_the_result_filter_matches_the_result_column(wanted, ticker):
    rows = [_row("UP", gap_pct=0.1, open_price=100.0, close_price=110.0),
            _row("DOWN", gap_pct=0.1, open_price=100.0, close_price=90.0),
            _row("SAME", gap_pct=0.1, open_price=100.0, close_price=100.0)]
    assert [r.ticker for r in apply_filters(rows, result=wanted)] == [ticker]


def test_the_result_filter_can_find_the_days_that_are_not_scored_yet():
    rows = [_row("DONE"), _row("PENDING", open_price=None, close_price=None)]
    assert [r.ticker for r in apply_filters(rows, result=RESULT_PENDING)] == ["PENDING"]


def test_the_filters_compose():
    rows = [_row("KEEP"), _row("WRONGSRC", source=SOURCE_YFINANCE),
            _row("WRONGNEWS", news_found="no news found"),
            _row("WRONGRESULT", close_price=90.0)]
    kept = apply_filters(rows, verified=VERIFIED_ONLY, news=NEWS_WITH, result=CARRIED_ON)
    assert [r.ticker for r in kept] == ["KEEP"]


def test_an_unrecognised_choice_filters_nothing_rather_than_everything():
    """A typo in a widget must not silently empty the page."""
    rows = [_row("A"), _row("B")]
    assert apply_filters(rows, verified="???", news="???", result="???") == rows


def test_every_offered_choice_is_a_choice_the_filter_understands():
    """The widget's options and the function's branches cannot drift apart."""
    rows = [_row("A"), _row("B", source=SOURCE_YFINANCE, news_found="no news found",
                            close_price=90.0)]
    for choice in VERIFIED_CHOICES:
        apply_filters(rows, verified=choice)
    for choice in NEWS_CHOICES:
        apply_filters(rows, news=choice)
    for choice in RESULT_CHOICES:
        apply_filters(rows, result=choice)


def test_a_filter_that_hides_rows_says_so():
    """A table quietly showing three of seventeen rows lies by omission."""
    assert filter_caption(17, 17) == "Showing all 17 candidate(s)."
    assert filter_caption(3, 17) == ("Showing 3 of 17 candidate(s) — filters are hiding 14.")


def test_the_caption_survives_an_empty_day():
    assert "0" in filter_caption(0, 0)


# --------------------------------------------------------------------------- #
# item 4(c) — how to read it
# --------------------------------------------------------------------------- #
def test_how_to_read_explains_the_columns_that_are_easy_to_misread():
    terms = {term for term, _ in HOW_TO_READ}
    for expected in ("Gap", "Verified", "Rel. volume", "Result"):
        assert expected in terms


def test_how_to_read_says_that_n_a_is_not_a_low_number():
    """The single most misreadable cell on the page."""
    meaning = dict(HOW_TO_READ)["Rel. volume"]
    assert "never measured" in meaning
    assert "not that it was low" in meaning


def test_how_to_read_repeats_that_nothing_here_is_advice():
    assert any("advice" in meaning for _term, meaning in HOW_TO_READ)


# --------------------------------------------------------------------------- #
# item 4 — the Deploy button, and ONLY that
# --------------------------------------------------------------------------- #
def test_only_the_deploy_button_is_hidden():
    """``app.py`` carries this scar in writing: a past chrome-strip took out the toolbar menu
    and the sidebar collapse toggle. Deploy is the only control a local read-only viewer has no
    use for, so it is the only one hidden."""
    source = _APP.read_text(encoding="utf-8")
    assert '[data-testid="stAppDeployButton"] {display: none' in source
    for control in ("stToolbar", "stMainMenu", "stSidebarCollapseButton",
                    "stSidebarCollapsedControl"):
        assert control in source


def test_the_controls_are_forced_visible_in_the_same_breath():
    source = _APP.read_text(encoding="utf-8")
    block = source.split('stAppDeployButton')[1].split("</style>")[0]
    assert "visibility: visible !important" in block


def test_nothing_hides_the_whole_header_or_toolbar():
    source = _APP.read_text(encoding="utf-8")
    assert "stHeader" not in source or "display: none" not in source.split("stHeader")[1][:80]


# --------------------------------------------------------------------------- #
# item 5 — the other tabs read alike
# --------------------------------------------------------------------------- #
def test_the_scorecard_says_how_far_off_an_answer_it_is():
    card = score({DAY: [_row()]})
    assert scorecard_progress(card) == "1 of 40 trading days"


def test_the_progress_line_counts_scored_days_not_logged_days():
    unfilled = [_row("A", open_price=None, close_price=None)]
    card = score({DAY: unfilled, DAY - timedelta(days=1): [_row("B")]})
    assert scorecard_progress(card) == "1 of 40 trading days"


def test_an_edge_is_coloured_the_same_way_a_gap_is():
    """Same convention on both tables, so the page reads as one thing."""
    assert points_cell(0.29).startswith(":green[")
    assert points_cell(-0.29).startswith(":red[")
    assert points_cell(0.0) == "+0 points"
    assert points_cell(None) == "—"
    assert gap_cell(0.29).startswith(":green[")
    assert gap_cell(-0.29).startswith(":red[")


def test_the_checkpoint_table_has_the_same_markdown_shape_as_the_day_table():
    card = score({DAY: [_row()]})
    checkpoints = checkpoint_markdown(card).splitlines()
    day = table_markdown([_row()]).splitlines()
    assert checkpoints[0].startswith("| ") and checkpoints[0].endswith(" |")
    assert set(checkpoints[1].strip("|").split("|")) == {"---"}
    assert set(day[1].strip("|").split("|")) == {"---"}
    assert len(checkpoints) == 2 + len(card.checkpoints)


def test_the_control_group_uses_the_very_same_table_builder():
    """Item 5: the same columns everywhere. One builder means they cannot drift."""
    control = table_markdown([_row("CTRL", group=GROUP_BASELINE)])
    assert control.splitlines()[0] == "| " + " | ".join(COLUMNS) + " |"


# --------------------------------------------------------------------------- #
# app level — one day selector, and the filters actually filter
# --------------------------------------------------------------------------- #
def _app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("GAP_LEDGER_ROOT", str(tmp_path))
    return AppTest.from_file(str(_APP), default_timeout=120).run()


def _said(at) -> str:
    return " ".join(str(getattr(e, "value", "")) for e in
                    list(at.caption) + list(at.markdown) + list(at.subheader)
                    + list(at.info) + list(at.warning))


def test_the_left_panel_has_its_three_sections(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("AAA")], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    assert not at.exception
    headings = [e.value for e in at.sidebar.subheader]
    assert headings == ["Day", "Filters", "How to read it"]


def test_there_is_exactly_one_day_selector_and_it_is_in_the_panel(tmp_path, monkeypatch):
    """Two day pickers would be two answers to the same question."""
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("AAA")], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    assert not at.exception
    day_pickers = [w for w in at.selectbox if isinstance(w.value, date)]
    assert len(day_pickers) == 1
    assert day_pickers[0].key == "panel_day"


def test_the_filters_are_offered_in_the_panel(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("AAA")], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    labels = {w.label for w in list(at.sidebar.radio) + list(at.sidebar.selectbox)}
    assert {"Verified", "News", "Result"} <= labels


def test_a_filter_hides_rows_and_the_page_says_how_many(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("KEEP"), _row("HIDE", source=SOURCE_YFINANCE)], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    assert "Showing all 2 candidate(s)." in _said(at)

    at.sidebar.radio(key="filter_verified").set_value(VERIFIED_ONLY).run()
    said = _said(at)
    assert "Showing 1 of 2 candidate(s)" in said
    assert "filters are hiding 1" in said


def test_filtering_everything_out_says_so_rather_than_showing_a_blank_page(tmp_path,
                                                                          monkeypatch):
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("ONLYIB")], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    at.sidebar.radio(key="filter_verified").set_value(UNVERIFIED_ONLY).run()
    assert not at.exception
    assert "No candidate matches these filters" in _said(at)


def test_the_commands_are_collapsed_rather_than_shouted(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("AAA")], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    labels = [e.label for e in at.sidebar.expander]
    assert "Commands" in labels
    assert "What the columns mean" in labels
    # ``proto.expanded`` is where the collapsed state actually lives — the element itself has
    # no ``expanded`` attribute, which is the sort of thing worth writing down once.
    assert all(e.proto.expanded is False for e in at.sidebar.expander)
    commands = next(e for e in at.sidebar.expander if e.label == "Commands")
    # Copyable: st.code renders with Streamlit's own copy button.
    assert any(RUN in str(c.value) for c in commands.code
               for RUN in ("aristos_council.gap_ledger run",))


def test_the_scorecard_progress_line_reaches_the_page(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    write_day(DAY, [_row("AAA")], root=tmp_path)
    at = _app(tmp_path, monkeypatch)
    assert "1 of 40 trading days" in _said(at)
