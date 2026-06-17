"""Tests for Council Station (app.py) pure handlers.

app.py imports streamlit, which lives in the optional ``ui`` extra and is NOT a
test dependency — so this module skips cleanly when streamlit isn't installed,
and runs the assertions where it is. Only pure, non-Streamlit helpers are
exercised here (the UI rendering itself is integration-tested by running it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import app  # noqa: E402
from aristos_council.data.adapter import DataUnavailable  # noqa: E402


def test_friendly_error_maps_data_unavailable_to_message():
    msg = app._friendly_error(DataUnavailable("delisted / empty frame"), "ZZZZ")
    assert msg == "No data found for ZZZZ — check the symbol."


def test_friendly_error_uses_the_ticker_in_the_message():
    assert "BRK-B" in app._friendly_error(DataUnavailable("x"), "BRK-B")


def test_friendly_error_passes_through_unexpected_exceptions():
    # Non-DataUnavailable errors return None so the UI shows the full traceback
    # rather than masking a real bug behind a friendly message.
    assert app._friendly_error(ValueError("boom"), "JNJ") is None
    assert app._friendly_error(RuntimeError("no key"), "JNJ") is None


# --------------------------------------------------------------------------- #
# Provenance prose stripper — patterns taken from saved JNJ/MO reports.
# Display-only: stored text keeps the call_ids; this just cleans the view.
# --------------------------------------------------------------------------- #
def test_strip_provenance_removes_real_callid_parentheticals():
    cases = [
        # (call_id: <id>, <field>) — colon form
        ("Payout is healthy (call_id: f53d7013627c, payout_ratio) and stable.",
         "Payout is healthy and stable."),
        # rich field reference with assignments and semicolons
        ("Yield misses (call_id: 8d39404e0e90, criteria[0].passed = false; "
         "observed = 0.02248982485973189; threshold = 0.025) the floor.",
         "Yield misses the floor."),
        # bare (call_id <id>) — no colon
        ("Streak unverifiable (call_id 8d39404e0e90).",
         "Streak unverifiable."),
        # [call_id <id>] — bracket form
        ("Coverage constructive [call_id 1db8ae4fbf65].",
         "Coverage constructive."),
    ]
    for raw, want in cases:
        assert app.strip_provenance(raw) == want


def test_strip_provenance_handles_nested_parens_in_citation():
    # A quoted headline inside the citation contains its own '(...)'.
    raw = ('News skews positive (call_id 11a7564d5ce2, item 2: "Assessing '
           'Johnson & Johnson (JNJ) prospects") overall.')
    assert app.strip_provenance(raw) == "News skews positive overall."


def test_strip_provenance_is_noop_on_clean_text():
    assert app.strip_provenance("No citations here.") == "No citations here."
    assert app.strip_provenance("") == ""
    assert app.strip_provenance(None) is None


def test_run_label_shape():
    from datetime import datetime, timezone

    from aristos_council.persistence.reports import RunReport
    from aristos_council.state import Decision, Recommendation

    r = RunReport(
        ticker="MO", run_at=datetime(2026, 6, 12, 13, 42, tzinfo=timezone.utc),
        strategy_id="dividend_aristocrats_v1",
        decision=Decision(recommendation=Recommendation.HOLD, confidence=0.55,
                          rationale="r"),
    )
    label = app._run_label(r)
    assert label.startswith("MO · ")
    assert "HOLD 0.55" in label
    # local time (Europe/Berlin = UTC+2 in June): 13:42 UTC -> 15:42
    assert "15:42" in label
    # verdict color dot present (selector can't render hex)
    assert app._VERDICT_DOT["HOLD"] in label


def test_verdict_hex_is_the_only_semantic_palette():
    assert app._verdict_hex("BUY") == "#2E7D32"
    assert app._verdict_hex("hold") == "#B8860B"   # case-insensitive
    assert app._verdict_hex("SELL") == "#B23B3B"
    assert app._verdict_hex(None) == "#8A8A8A"     # neutral fallback


def test_favicon_is_svg_data_uri():
    uri = app._favicon()
    assert uri.startswith("data:image/svg+xml;base64,")


# --------------------------------------------------------------------------- #
# Rendering fixes — tested against the REAL saved BRK-B and MO reports.
# --------------------------------------------------------------------------- #
import glob  # noqa: E402

from aristos_council.persistence.reports import load_report  # noqa: E402

_REPORTS = Path(__file__).resolve().parents[1] / "reports"


def _saved(ticker):
    return load_report(sorted(glob.glob(str(_REPORTS / ticker / "*.json")))[-1])


def test_brkb_rationale_preserves_markdown_line_structure():
    out = app._prose(_saved("BRK-B").decision.rationale, show_provenance=False)
    # NOT flattened into one paragraph
    assert out.count("\n") > 10
    assert "**Strategy Mandate" in out
    assert "\n\n**Screen Results**" in out          # bold "header" on its own line
    assert "\n1. **min_dividend_yield**" in out      # numbered list survives
    assert "\n2. **max_payout_ratio**" in out


def test_brkb_field_paths_move_behind_provenance_toggle():
    r = _saved("BRK-B")
    default = app._prose(r.decision.rationale, show_provenance=False)
    raw = app._prose(r.decision.rationale, show_provenance=True)
    assert "criteria[0].passed" not in default       # stripped from default view
    assert "call_id" not in default                  # and no call_id plumbing
    assert "criteria[0].passed = false" in raw       # kept in the toggle view
    # the plain statement around it survives
    assert "observed 0.0 against a threshold of 0.025" in default


def test_dollar_signs_escaped_so_currency_survives_markdown():
    disp = app._render_prose(_saved("BRK-B").decision.rationale, False)
    assert "\\$1.048 trillion" in disp               # escaped, not eaten
    assert disp.count("$") == disp.count("\\$")       # every $ is escaped


def test_mo_rationale_preserves_table_and_strips_inline_path():
    r = _saved("MO")
    out = app._prose(r.decision.rationale, show_provenance=False)
    assert "| Criterion | Result | Observed | Threshold |" in out  # table header
    assert "\n| min_dividend_yield |" in out                       # a table row
    assert "criteria[1].passed" not in out                         # inline path gone
    assert "call_id" not in out
    disp = app._render_prose(r.decision.rationale, False)
    assert disp.count("$") == disp.count("\\$")


def test_strategy_dir_is_absolute_and_anchored_to_the_app_file():
    assert app.STRATEGIES_DIR.is_absolute()
    assert app.STRATEGIES_DIR == app.ROOT / "strategies"
    assert app.ROOT == _APP.parent


def test_strategy_discovery_is_cwd_independent(monkeypatch, tmp_path):
    # Launch cwd must not matter: both strategies are found from anywhere.
    monkeypatch.chdir(tmp_path)
    ids = [s.id for _, _, s in app.list_strategy_options(app.STRATEGIES_DIR)]
    assert {"dividend_aristocrats_v1", "growth_v1"} <= set(ids)


def test_dropdown_lists_all_live_strategies():
    # 4C: growth_v1 is lit up — every live strategy is selectable.
    options = app.list_strategy_options(app.STRATEGIES_DIR)
    ids = [s.id for _, _, s in options]
    assert "dividend_aristocrats_v1" in ids
    assert "growth_v1" in ids


_APP = Path(__file__).resolve().parents[1] / "app.py"


def _markdown_blob(at) -> str:
    return "\n".join(m.value for m in at.markdown if isinstance(m.value, str))


_MSFT_PRE_4E = _REPORTS / "MSFT" / "2026-06-14T13-29-49Z.json"


def test_bare_callid_in_key_figures_block_is_stripped():
    # The Decision rationale's "Key Figures (Provenance)" block uses bare
    # "call_id <hex>, <field>" — must be stripped from displayed prose.
    rat = load_report(_MSFT_PRE_4E).decision.rationale
    assert "call_id" in rat                       # present in the raw/toggle view
    out = app.strip_provenance(rat)
    assert "call_id" not in out                   # gone from the default view
    assert "0.1242" in out                        # the observed value survives


def test_data_quality_summary_line():
    audit = load_report(_MSFT_PRE_4E).provenance_audit
    assert app._dq_summary(audit) == \
        "7 provenance issues: 5 mismatches, 2 unresolvable"


def test_data_quality_violations_grouped_by_tool():
    audit = load_report(_MSFT_PRE_4E).provenance_audit
    groups = app._group_violations(audit["violations"])
    # every violation is accounted for, and repeats collapse into fewer lines
    assert sum(len(items) for _, items in groups) == 7
    assert len(groups) < 7
    # headers are "<count> <kind> citing <tool>"
    assert all(h.split()[0].isdigit() for h, _ in groups)


def test_data_quality_banner_renders_summary_with_expander():
    # Integration: browsing the pre-4E MSFT run, the data_quality flag shows the
    # one-line summary and a "Show provenance issues" expander (not a raw dump).
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=90).run()
    next(s for s in at.selectbox if s.label.startswith("Runs for")).set_value("MSFT")
    at.run()
    run_sel = next(s for s in at.selectbox if s.label == "Run")
    run_sel.set_value(len(run_sel.options) - 1)      # oldest = pre-4E (7 issues)
    at.run()
    assert not at.exception
    assert "7 provenance issues" in _markdown_blob(at)
    assert any(e.label == "Show provenance issues" for e in at.expander)


def test_screen_chrome_css_keeps_controls_reachable():
    # On screen the ONLY hide is the footer; the menu + sidebar toggle are
    # explicitly forced visible (never hidden). Aggressive hides are allowed
    # inside @media print, which is excluded here.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    blob = _markdown_blob(at)
    screen_css = blob.split("@media print")[0]   # on-screen rules only
    assert "footer {visibility: hidden;}" in screen_css   # footer hide is fine
    assert "display: none" not in screen_css              # nothing else hidden
    # controls are explicitly forced visible
    assert "visibility: visible !important;" in screen_css
    assert "#MainMenu" in screen_css                      # menu kept reachable
    assert "Sidebar" in screen_css                        # sidebar toggle kept


def test_toolbar_mode_keeps_menu_reachable():
    # The ⋮ menu is gated server-side by toolbarMode: "viewer"/"minimal" hide it
    # entirely (no CSS restores it). Only "auto" (localhost) / "developer" render
    # the menu + its Settings/theme switch, so config.toml must use one of those.
    import tomllib
    cfg = tomllib.loads(
        (_APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["client"]["toolbarMode"] in ("auto", "developer")


def test_human_number_formats_large_thresholds():
    assert app._human_number(10_000_000_000) == "10,000,000,000 ($10B)"
    assert app._human_number(5_000_000_000) == "5,000,000,000 ($5B)"
    assert app._human_number(0.025) is None          # small values: no humanizing
    assert app._human_number(25) is None


def test_strategy_tab_renders_dividend_criteria_by_default():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    blob = _markdown_blob(at)
    assert "Minimum dividend yield" in blob           # registry label rendered
    assert "Minimum revenue CAGR" not in blob


def test_strategy_tab_switches_to_growth_criteria_generically():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    sb = next(s for s in at.selectbox if s.label == "Strategy")
    growth_label = next(o for o in sb.options if "growth_v1" in o)
    sb.set_value(growth_label)
    at.run()
    assert not at.exception
    blob = _markdown_blob(at)
    # dividend and growth declare different criteria -> the form re-renders with
    # different fields automatically, no strategy-specific UI code.
    assert "Minimum revenue CAGR" in blob
    assert "Minimum ROIC" in blob
    assert "Minimum dividend yield" not in blob


def test_growth_run_is_triggerable_and_reports_browsable_under_growth():
    # End-to-end UI check without a real API call: selecting growth + a ticker +
    # the cost ack makes the Run button live (run_council would receive the
    # growth path — see the routing test), and the report browser still works
    # (it is strategy-independent, so any saved ticker is browsable).
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    sb = next(s for s in at.selectbox if s.label == "Strategy")
    sb.set_value(next(o for o in sb.options if "growth_v1" in o))
    next(t for t in at.text_input if t.label == "Ticker").set_value("JNJ")
    next(c for c in at.checkbox if "costs real credits" in c.label).set_value(True)
    at.run()
    assert not at.exception
    run_btn = next(b for b in at.button if "Run council" in b.label)
    assert run_btn.disabled is False                  # growth run is triggerable
    assert any(s.label.startswith("Runs for") for s in at.selectbox)  # browsable


def test_selecting_growth_routes_the_growth_strategy_path():
    # The label->path map (what the sidebar selectbox drives) must route the
    # growth label to growth_v1.yaml, which loads the growth strategy.
    from aristos_council.strategy.loader import load_strategy

    options = app.list_strategy_options(app.STRATEGIES_DIR)
    label_to_path = {label: path for label, path, _ in options}
    growth_label = next(label for label, _, s in options if s.id == "growth_v1")
    path = label_to_path[growth_label]
    assert path.name == "growth_v1.yaml"
    assert load_strategy(path).id == "growth_v1"


# --------------------------------------------------------------------------- #
# Strategy tab cleanup: one strategy at a time, distinct sections, locked params
# --------------------------------------------------------------------------- #
def _strategy_tab_text(at) -> str:
    """All textual output (headings, markdown, captions) — for asserting the
    tab's structure regardless of which element type carries each string."""
    parts = []
    for attr in ("title", "header", "subheader", "markdown", "caption"):
        for el in getattr(at, attr, []):
            v = getattr(el, "value", None)
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts)


def test_strategy_tab_header_names_selected_strategy_and_switches():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert "Viewing: Dividend Aristocrats (dividend_aristocrats_v1)" \
        in _strategy_tab_text(at)
    # switching the dropdown swaps the WHOLE tab to the other strategy
    sb = next(s for s in at.selectbox if s.label == "Strategy")
    sb.set_value(next(o for o in sb.options if "growth_v1" in o))
    at.run()
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Viewing: Growth at a Reasonable Price (growth_v1)" in txt
    assert "Dividend Aristocrats (dividend_aristocrats_v1)" not in txt  # not both


def test_strategy_tab_has_distinct_criteria_policy_and_veto_sections():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    txt = _strategy_tab_text(at)
    assert "Criteria" in txt and "Policy" in txt and "Veto gate" in txt
    # the Policy flag lives in its own section as a checkbox, not a criterion box
    assert any("Partial pass allows HOLD" in c.label for c in at.checkbox)


def test_peg_cagr_window_surfaced_as_locked_even_in_edit_mode():
    # The PEG criterion's in-house CAGR window was a HIDDEN verdict input; it is
    # now shown read-only/locked (disabled + 🔒) alongside the revenue-CAGR one,
    # and stays locked even when editing (it is not strategy-configurable).
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    sb = next(s for s in at.selectbox if s.label == "Strategy")
    sb.set_value(next(o for o in sb.options if "growth_v1" in o))
    at.run()
    next(t for t in at.toggle if "Edit as a new version" in t.label).set_value(True)
    at.run()
    assert not at.exception
    windows = [n for n in at.number_input if "CAGR window" in n.label]
    assert len(windows) == 2                          # revenue_cagr + max_peg_ratio
    assert all(n.disabled for n in windows)           # locked even in edit mode
    assert all("🔒" in n.label for n in windows)
    # contrast: an editable threshold IS enabled in edit mode
    thresholds = [n for n in at.number_input if n.label.startswith("Threshold")]
    assert thresholds and any(not n.disabled for n in thresholds)


def test_available_tickers_lists_every_ticker_on_disk():
    tickers = app._available_tickers(app.REPORTS_DIR)
    assert tickers == sorted(tickers)                 # sorted
    # all tickers with saved reports are browsable, regardless of the sidebar
    assert {"BRK-B", "JNJ", "MO"} <= set(tickers)


def test_screen_table_rows_map_pass_fail_noteval():
    rows = app._screen_table_rows({"criteria": [
        {"name": "min_dividend_yield", "passed": True, "observed": 0.05,
         "threshold": 0.025},
        {"name": "max_payout_ratio", "passed": False, "observed": 0.9,
         "threshold": 0.75},
        {"name": "min_market_cap", "passed": None, "observed": None,
         "threshold": 1e10},
    ]})
    assert [r["Status"] for r in rows] == ["PASS", "FAIL", "NOT-EVAL"]
    assert rows[0]["Criterion"] == "min_dividend_yield"
    assert rows[2]["Observed"] is None


def test_screen_table_rows_empty_when_no_screen():
    assert app._screen_table_rows(None) == []
    assert app._screen_table_rows({}) == []
