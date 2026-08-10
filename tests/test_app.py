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
    # INSUFFICIENT_EVIDENCE has its own NON-directional slate (not green/amber/red)
    ie = app._verdict_hex("INSUFFICIENT_EVIDENCE")
    assert ie == "#5B6B7B"
    assert ie not in {"#2E7D32", "#B8860B", "#B23B3B"}
    assert "INSUFFICIENT_EVIDENCE" in app._VERDICT_DOT


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


def _legacy_app(timeout: int = 60):
    """Run the app with the 'Show legacy tools' toggle ON — legacy surfaces (the
    single-ticker council, Report/History, Strategy editor) are hidden by DEFAULT, so
    any test that exercises them must opt in. Session state persists across at.run()."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP), default_timeout=timeout)
    at.session_state["show_legacy"] = True
    return at.run()


# --------------------------------------------------------------------------- #
# Universe Run tab — schema-split dropdowns + pure render helpers (Sprint)
# --------------------------------------------------------------------------- #
def test_rank_strategy_options_lists_only_rank_strategies():
    ids = [s.id for _, _, s in app.list_rank_strategy_options(app.STRATEGIES_DIR)]
    assert {"conservative_plus_v1", "magic_formula_v1",
            "magic_formula_momentum_v1"} <= set(ids)
    # council + lens strategies never appear in the rank dropdown
    assert "growth_v1" not in ids and "magic_value_screen_v1" not in ids


def test_single_ticker_dropdown_excludes_rank_and_lens():
    ids = [s.id for _, _, s in app.list_strategy_options(app.STRATEGIES_DIR)]
    assert "magic_formula_v1" not in ids               # rank -> Universe Run tab only
    assert "conservative_screen_v1" not in ids         # lens -> hidden


def test_the_one_flow_parses_tickers_through_the_editor_parser():
    # FUND-UI-2: one flow, one parser. app._parse_universe (the old Custom-paste
    # parser) is gone — the editor's comment-tolerant parse_ticker_lines is a superset of
    # it and now serves the single ticker box, so a pasted line keeps working exactly as
    # it did while `# comments` also survive.
    from aristos_council.universe_editor import parse_ticker_lines

    assert not hasattr(app, "_parse_universe")
    assert parse_ticker_lines("aapl, msft\nGOOGL aapl , ,brk.b") == \
        ["AAPL", "MSFT", "GOOGL", "BRK.B"]              # upper, de-duped, order kept
    assert parse_ticker_lines("") == []


def test_saved_list_labels_disambiguate_a_shared_name():
    from aristos_council.universe import Universe

    a = Universe(id="mine_v1", display_name="My Portfolio", tickers=["AAPL", "MSFT"])
    b = Universe(id="mine_v2", display_name="My Portfolio", tickers=["NVDA", "AMD"])
    solo = Universe(id="other_v1", display_name="Watchlist", tickers=["XOM"])
    labels = app.saved_list_labels([a, b, solo])
    assert len(set(labels)) == 3                       # a label names exactly one list
    assert labels[0].endswith("(mine_v1)") and labels[1].endswith("(mine_v2)")
    assert labels[2] == "Watchlist · 1 names"          # unique -> left alone


def test_run_problems_gate_the_one_run_button():
    # Every reason the Run button can be disabled, as plain sentences — pure, so the one
    # flow's guards are tested rather than eyeballed in a browser.
    assert app.run_problems(["AAPL"], n_strategies=1, ranker_only=True,
                            has_key=False) == []
    assert "Pick at least one strategy." in app.run_problems(
        ["AAPL"], n_strategies=0, ranker_only=True, has_key=True)
    assert "Add at least one ticker." in app.run_problems(
        [], n_strategies=1, ranker_only=True, has_key=True)
    too_many = app.run_problems([f"T{i}" for i in range(app.UNIVERSE_CAP + 1)],
                                n_strategies=1, ranker_only=True, has_key=True)
    assert any("too large" in p for p in too_many)
    # An LLM run without a key is blocked; the free ranker path stays available.
    assert any("ANTHROPIC_API_KEY" in p for p in app.run_problems(
        ["AAPL"], n_strategies=1, ranker_only=False, has_key=False))
    assert app.run_problems(["AAPL"], n_strategies=3, ranker_only=True,
                            has_key=False) == []          # multi-lens cannot spend


def test_estimate_shortlist_size_tracks_the_cut():
    from aristos_council.strategy.rank_loader import load_rank_strategy
    magic = load_rank_strategy(app.STRATEGIES_DIR / "magic_formula_v1.yaml")
    assert app._estimate_shortlist_size(0, magic) == 0
    assert app._estimate_shortlist_size(20, magic) == 4   # quintile ~ n/5
    assert app._estimate_shortlist_size(2, magic) == 1     # never below 1 for n>0


def test_ranked_rows_marks_imputed_factors_with_a_star():
    from aristos_council.rank_engine import RankedTicker
    rt = RankedTicker(
        ticker="A", factor_ranks={"earnings_yield": 1.0, "net_payout_yield": 2.0},
        factor_values={}, combined_rank=3.0, universe_size=3, verdict="buy",
        imputed_factors=["net_payout_yield"])
    rows, factors = app._ranked_rows([rt])
    assert factors == ["earnings_yield", "net_payout_yield"]
    row = rows[0]
    assert row["Verdict"] == "BUY"
    # RANK-DISPLAY-1: ordinal position first, rank-SUM as detail against its bounds
    # (2 factors, cohort of 1 -> best 2 · worst 2; score = combined 3).
    assert row["Position (score)"] == "#1 of 1 · score 3 (best 2 · worst 2)"
    assert row["earnings_yield"] == "1"                 # present, no star
    assert row["net_payout_yield"] == "2*"              # imputed -> star


def test_universe_markdown_has_sections_from_the_result():
    from aristos_council.pipeline import RankPipelineResult
    from aristos_council.rank_engine import RankedTicker
    rt = RankedTicker(ticker="A", factor_ranks={"earnings_yield": 1.0},
                      factor_values={}, combined_rank=1.0, universe_size=2,
                      verdict="buy")
    result = RankPipelineResult(
        ranked=[rt], excluded=[("C", "screen: min_roic (observed 0.08 vs 0.12)")],
        unrateable=[("DEAD", "UNRATEABLE: no data — possibly delisted")],
        narratives={"A": "ranked #1 on ROIC."},
        header="Verdict: deterministic ranker.  Narrative: LLM (non-judging).",
        meta={"rank_strategy_id": "magic_formula_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "council_mode": "narrator", "ranker_only": False,
              "universe_size": 3, "ranked_count": 1, "shortlist": ["A"],
              "est_cost": 0.19},
        council_mode="narrator")
    md = app._universe_markdown(result)
    assert "# Universe run — magic_formula_v1" in md
    assert "## Ranked (verdict of record)" in md
    assert "Position (score)" in md                     # RANK-DISPLAY-1 header
    # the ranked row for A: ordinal position first (1 factor, cohort of 1), then verdict
    assert "#1 of 1 · score 1 (best 1 · worst 1) | A | BUY |" in md
    assert "## Excluded" in md and "min_roic" in md
    assert "## Unrateable" in md and "DEAD" in md
    assert "## Narrative" in md and "ranked #1 on ROIC." in md


def test_universe_markdown_records_the_exact_cohort_graded():
    # FUND-UI-2: a saved list is editable, so the record — not the id — is what keeps a
    # past run interpretable. The membership rides the canonical markdown record.
    from aristos_council.pipeline import RankPipelineResult
    result = RankPipelineResult(
        ranked=[], excluded=[], unrateable=[], narratives={},
        header="Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran).",
        meta={"rank_strategy_id": "magic_formula_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "universe_id": "my_portfolio_v1", "council_mode": "ranker-only",
              "ranker_only": True, "universe_size": 3, "ranked_count": 0,
              "shortlist": [], "est_cost": 0.0,
              "universe_members": ["AAPL", "MSFT", "NVDA"],
              "universe_member_hash": "abcd1234"},
        council_mode="ranker-only")
    md = app._universe_markdown(result)
    assert "## Cohort graded (exact membership)" in md
    assert "`my_portfolio_v1` · 3 names · members `abcd1234`" in md
    assert "AAPL, MSFT, NVDA" in md


def test_universe_markdown_omits_the_cohort_section_for_a_pre_fund_ui_2_record():
    # An older saved result carries no members — the section is skipped rather than
    # rendering an empty, misleading "graded nothing" block.
    from aristos_council.pipeline import RankPipelineResult
    result = RankPipelineResult(
        ranked=[], excluded=[], unrateable=[], narratives={},
        header="h",
        meta={"rank_strategy_id": "magic_formula_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "universe_id": "growth_40_v1", "council_mode": "ranker-only",
              "ranker_only": True, "universe_size": 40, "ranked_count": 0,
              "shortlist": [], "est_cost": 0.0},
        council_mode="ranker-only")
    assert "## Cohort graded" not in app._universe_markdown(result)


def test_strategy_picker_order_baseline_label_and_run_heading():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    opts = list(_strategy_picker(at).options)
    assert "momentum" in opts[0].lower()                        # flagship first
    # the baseline (magic_formula_v1) is HIDDEN by default now (ITEM 2) — it carries its
    # 'baseline — for comparison' label only under the validation toggle (asserted in
    # test_validation_assets_revealed_when_toggle_on).
    assert not any("baseline" in o.lower() for o in opts)
    heads = " ".join(str(getattr(e, "value", "")) for e in at.subheader)
    assert "Run — pick strategies, pick tickers, run" in heads and "v2" not in heads


def test_confirmation_line_states_strategy_universe_and_mode():
    m = {"rank_strategy_id": "magic_formula_v1", "universe_id": "growth_40_v1",
         "council_mode": "ranker-only"}
    assert app._confirmation_line(m) == \
        "Running magic_formula_v1 on growth_40_v1 in ranker-only."
    # ad-hoc universe id (with its hash) is carried through
    m2 = {"rank_strategy_id": "s", "universe_id": "adhoc:abcd1234",
          "council_mode": "narrator"}
    assert "adhoc:abcd1234" in app._confirmation_line(m2)


def test_confirmation_line_is_in_the_persisted_markdown():
    from aristos_council.pipeline import RankPipelineResult
    result = RankPipelineResult(
        ranked=[], excluded=[], unrateable=[], narratives={},
        header="Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran).",
        meta={"rank_strategy_id": "magic_formula_momentum_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "universe_id": "growth_40_v1", "council_mode": "ranker-only",
              "ranker_only": True, "universe_size": 40, "ranked_count": 0,
              "shortlist": [], "est_cost": 0.0}, council_mode="ranker-only")
    md = app._universe_markdown(result)
    assert "Running magic_formula_momentum_v1 on growth_40_v1 in ranker-only." in md


def _strategy_picker(at):
    """The ONE strategy picker of the run flow (FUND-UI-2) — a multiselect, so several
    lenses can grade the same list in one go."""
    return next(m for m in at.multiselect if m.label == "Strategies")


def test_run_tab_renders_the_one_flow():
    # FUND-UI-2: the whole run UX is strategies + tickers + run. Their presence proves
    # the tab rendered; no run is triggered, so nothing hits the network.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    picker = _strategy_picker(at)
    # Options are the FRIENDLY display names (ITEM 1) — the technical id is demoted to a
    # caption, so it never appears in the label (no ids/underscores/_v1).
    assert any("Value + Momentum" in o for o in picker.options)
    assert not any("_" in o for o in picker.options)
    assert picker.value                                    # a strategy is pre-selected
    assert any(t.label.startswith("Tickers") for t in at.text_area)
    assert any(s.label == "List" for s in at.selectbox)
    assert any(b.label.startswith("▶ Run") for b in at.button)


def test_the_separate_universe_edit_run_section_is_gone():
    # FUND-UI-2: ONE flow. The old bottom "Universe Editor" expander had its own clone
    # selector, its own Run-once button and its own save path — a second way to do the
    # same thing, which is what the issue asks to remove.
    import inspect

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert not hasattr(app, "render_universe_editor")
    assert "render_universe_editor" not in inspect.getsource(app.render_universe_tab)
    assert not any(s.label == "Start from" for s in at.selectbox)
    assert not any("Run once" in b.label for b in at.button)
    assert not any("Universe Editor" in str(e.label) for e in at.expander)
    # ...and exactly ONE run button in the flow (Company Check's own button is a
    # different tab's, and is excluded by label).
    assert len([b for b in at.button
                if b.label in ("▶ Run", "▶ Run ranker (free)")]) == 1


# --------------------------------------------------------------------------- #
# Legacy surfaces hidden by default behind the "Show legacy tools" toggle
# --------------------------------------------------------------------------- #
def _info_blob(at) -> str:
    return " ".join(str(getattr(e, "value", "")) for e in getattr(at, "info", []))


def _header_blob(at) -> str:
    return " ".join(str(getattr(e, "value", "")) for e in getattr(at, "header", []))


def test_legacy_hidden_by_default_and_toggle_defaults_off():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    # the toggle exists and defaults OFF
    legacy_toggle = next(t for t in at.toggle
                         if t.label == "Show validation & legacy tools")
    assert legacy_toggle.value is False
    assert at.session_state["show_legacy"] is False
    # NO legacy surface rendered: no council-run button, no legacy sidebar header, no
    # Strategy-editor scope banner, no legacy tabs — the legacy render paths never ran.
    assert not any("Run council" in b.label for b in at.button)
    assert "Run a council" not in _header_blob(at)
    assert "Edits council-strategy YAMLs" not in _info_blob(at)
    assert not any("Legacy" in str(t.label) for t in at.tabs)
    # the v2 product IS the landing (the run flow's strategy picker renders)
    assert any(m.label == "Strategies" for m in at.multiselect)


def test_legacy_surfaces_appear_when_toggle_on():
    at = _legacy_app(60)
    assert not at.exception
    assert any("Run council" in b.label for b in at.button)          # council flow back
    assert "Run a council" in _header_blob(at)                       # legacy sidebar back
    assert any("Report · Legacy" in str(t.label) for t in at.tabs)   # legacy tabs back


def _dropdown(at, label):
    return next(s for s in at.selectbox if s.label == label)


def test_validation_assets_hidden_by_default(monkeypatch):
    # ITEM 2 + UNI-1, as the one flow renders it (FUND-UI-2): toggle OFF (default) -> the
    # List selector offers "New list" plus the front-stage lists, and the strategy picker
    # offers the live strategies. Never-graded/validation assets and the ui:hidden
    # baseline stay hidden.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception

    lists = _dropdown(at, "List").options
    assert lists[0] == "New list"                                    # always startable
    assert not any("Validation Bench" in o for o in lists)           # trap bench hidden
    assert not any("Energy Watch" in o for o in lists)               # observation hidden

    rank = _strategy_picker(at).options
    assert not any("Classic Value" in o for o in rank)              # baseline hidden (ui: hidden)
    assert "Growth" in rank                                         # growth is live (4C)
    assert any("RAW" in o for o in rank)                            # canonical raw (RAW-1)
    assert any("Financials" in o for o in rank)                     # financials lens (FIN-1)
    assert any("Dividend ETFs" in o for o in rank)                  # ETF-1 dividend lens
    assert any("Growth ETFs" in o for o in rank)                    # ETF-1 growth lens
    assert any("ETF Index Tracker" in o for o in rank)              # ETFCORE-1 lens (UI-RENAME-1)
    assert not any("Core Market ETFs" in o for o in rank)           # old label gone
    # 5 stock lenses + 3 visible ETF lenses (dividend, growth, core [ETFCORE-1]).
    assert len(rank) == 8


def test_both_strategy_pickers_offer_the_same_live_strategies():
    # FUND-UI-2: the run flow's picker and the Company Check picker are the SAME
    # implementation now, so they offer the same set in the same order — a fix to one
    # can no longer leave the other behind.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    rank = _strategy_picker(at).options
    cc = _dropdown(at, "Strategy (lens screen + factors)").options
    assert list(rank) == list(cc)
    for opts in (rank, cc):
        assert "Growth" in opts                                      # plain names now
        assert any("RAW" in o for o in opts)                         # canonical raw
        assert any("Financials" in o for o in opts)                  # financials lens (FIN-1)
        assert not any("_" in o for o in opts)                       # display names, no ids
        # 5 stock lenses + 3 visible ETF lenses (dividend, growth, core [ETFCORE-1]).
        assert len(opts) == 8


def test_the_same_lists_are_offered_to_the_run_flow_and_company_check():
    # Both surfaces read the same list set through the same role-derived
    # visible_universes — the run flow offers them for editing, Company Check as factor
    # context. Neither invents its own set.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    lists = [o for o in _dropdown(at, "List").options if o != "New list"]
    ref = [o for o in _dropdown(at, "Reference universe (for factor context)").options
           if not o.startswith("(none")]
    assert {o.lstrip("⭐ ") for o in lists} == {o.lstrip("⭐ ") for o in ref}
    # the never-graded trap bench stays backstage in both (default toggle off)
    assert not any("Validation Bench" in o for o in lists)
    assert not any("Validation Bench" in o for o in ref)


def test_validation_assets_revealed_when_toggle_on():
    at = _legacy_app(60)
    assert not at.exception
    rank = _strategy_picker(at).options
    assert any("Classic Value" in o for o in rank)                  # baseline revealed


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
    at = _legacy_app(90)                              # Report browser is a legacy surface
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


def test_strategy_tab_renders_criteria_and_provenance_by_default():
    # 4C ITEM 3: the dynamic viewer renders the selected strategy's screen criteria and
    # the provenance footer, all from YAML.
    at = _legacy_app(60)
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Screen criteria" in txt
    assert "configs are versioned; strategies are never mutated" in txt   # provenance


def test_strategy_tab_switches_strategy_via_its_own_selector():
    # Switching the viewer's own "Strategy config" selector to the GARP rank strategy
    # renders rank factors + the verdict cut generically (no strategy-specific code).
    at = _legacy_app(60)
    sb = next(s for s in at.selectbox if s.label == "Strategy config")
    sb.set_value(next(o for o in sb.options if "growth_garp_v1" in o))
    at.run()
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Rank factors + verdict cut" in txt         # rank-only section rendered
    assert "quintile" in txt                          # the configured cut rule
    assert "growth_garp_v1" in txt                    # header names the switched-to strategy


def test_growth_run_is_triggerable_and_reports_browsable_under_growth():
    # End-to-end UI check without a real API call: selecting growth + a ticker +
    # the cost ack makes the Run button live (run_council would receive the
    # growth path — see the routing test), and the report browser still works
    # (it is strategy-independent, so any saved ticker is browsable).
    at = _legacy_app(60)
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


def test_strategy_tab_shows_gates_rationale_and_policy_glossary():
    # Switch to the flagship: its sector gate + rationale (post-send ITEM 2) and a policy
    # flag with a plain glossary meaning render — all from YAML, one strategy at a time.
    at = _legacy_app(60)
    sb = next(s for s in at.selectbox if s.label == "Strategy config")
    sb.set_value(next(o for o in sb.options if "magic_formula_momentum_v1" in o))
    at.run()
    assert not at.exception
    txt = _strategy_tab_text(at)
    assert "Gates" in txt and "Policy" in txt
    assert "not computable on a comparable basis for financials" in txt   # sector rationale
    assert "hard prefilter" in txt                     # prefilter_screen glossary meaning
    # only ONE strategy on screen at a time (the header names it)
    assert "growth_garp_v1" not in txt


def test_strategy_tab_lists_a_synthetic_strategy_with_zero_ui_changes(tmp_path):
    # ACCEPTANCE: a brand-new strategy YAML dropped into strategies/ renders fully via
    # strategy_detail with NO UI-code changes — every section derives from the YAML.
    from aristos_council.strategy.detail import strategy_detail

    (tmp_path / "synthetic_screen_v1.yaml").write_text(
        "\n".join([
            "id: synthetic_screen_v1",
            "name: Synthetic screen",
            "version: 1",
            "criteria:",
            "  - name: min_roic",
            "    threshold: 0.12",
            "  - name: min_price_momentum",
            "    threshold: 0.0",
            "",
        ]), encoding="utf-8")
    (tmp_path / "synthetic_v1.yaml").write_text(
        "\n".join([
            "id: synthetic_v1",
            "name: Synthetic",
            "display_name: Synthetic Demo",
            "version: 3",
            "created: '2026-07-09'",
            "description: A synthetic strategy for the acceptance test.",
            "factors:",
            "  - name: roic",
            "  - name: momentum_12m",
            "cut: top_k",
            "k: 5",
            "min_market_cap: 3000000000",
            "exclude_sectors:",
            "  - Financials",
            "sector_exclusion_rationale: banks are out.",
            "council_screen_strategy: synthetic_screen_v1",
            "prefilter_screen: true",
            "",
        ]), encoding="utf-8")

    d = strategy_detail("synthetic_v1", tmp_path)
    # 1 header
    assert d.display_name == "Synthetic Demo" and d.version == 3 and d.created == "2026-07-09"
    # 2 description
    assert d.description == "A synthetic strategy for the acceptance test."
    # 3 criteria (resolved from the referenced lens)
    assert d.screen_source == "lens: synthetic_screen_v1"
    assert any(c.name == "min_roic" for c in d.criteria)
    # 4 gates (sector + rationale, market cap)
    assert any(g.name == "sector" and "banks are out" in g.rationale for g in d.gates)
    assert any(g.name == "min_market_cap" for g in d.gates)
    # 5 factors + cut
    assert {f.name for f in d.factors} == {"roic", "momentum_12m"}
    assert "top_k" in d.cut_rule and "5" in d.cut_rule
    # 6 policy (glossary-sourced meaning)
    assert any(p.name == "prefilter_screen" and "hard prefilter" in p.meaning
               for p in d.policy)
    # 7 provenance
    assert d.path.endswith("synthetic_v1.yaml")


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


# --------------------------------------------------------------------------- #
# The one run flow (FUND-UI-2) — pick strategies, edit the list, run
# --------------------------------------------------------------------------- #
def test_ticker_list_is_editable_in_the_run_flow_and_savable():
    # The list editor IS the run flow now: one ticker box, plus save actions beside it.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    box = next(t for t in at.text_area if t.label.startswith("Tickers"))
    assert "# comments" in box.label                     # the comment-tolerant parser
    assert any("Save this list" in str(e.label) for e in at.expander)
    assert any(b.label == "Save changes" for b in at.button)
    assert any(b.label == "Save as new list" for b in at.button)
    assert any(t.label == "List name" for t in at.text_input)


def test_selecting_a_saved_list_loads_it_into_the_editor():
    # Selecting a list is how you load it — no separate "load into editor" step, and no
    # second run path for edited lists.
    from streamlit.testing.v1 import AppTest
    local_dir = app.UNIVERSES_DIR / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    f = local_dir / "apptest_load_v1.yaml"
    f.write_text(
        "id: apptest_load_v1\ndisplay_name: Apptest Load\n"
        "created: '2026-08-10'\nrationale: test\ntickers:\n  - AAPL\n  - MSFT\n",
        encoding="utf-8")
    try:
        at = AppTest.from_file(str(_APP), default_timeout=60).run()
        assert not at.exception
        dd = _dropdown(at, "List")
        label = next(o for o in dd.options if "Apptest Load (local)" in o)
        dd.set_value(label).run()
        assert not at.exception
        box = next(t for t in at.text_area if t.label.startswith("Tickers"))
        assert box.value.split() == ["AAPL", "MSFT"]
    finally:
        f.unlink(missing_ok=True)


def test_saved_local_universe_appears_in_both_selectors():
    # UNIED-1 Item 3: a saved local list is discovered front-stage (default toggle off)
    # in BOTH the run flow's List selector and the Company Check reference selector,
    # tagged "(local)". Written into the real (gitignored) universes/local/ then removed.
    from streamlit.testing.v1 import AppTest
    local_dir = app.UNIVERSES_DIR / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    f = local_dir / "apptest_local_v1.yaml"
    f.write_text(
        "id: apptest_local_v1\ndisplay_name: Apptest Local\n"
        "created: '2026-07-15'\nrationale: test\ntickers:\n  - AAPL\n  - MSFT\n",
        encoding="utf-8")
    try:
        at = AppTest.from_file(str(_APP), default_timeout=60).run()
        assert not at.exception
        uni = _dropdown(at, "List").options
        ref = _dropdown(at, "Reference universe (for factor context)").options
        assert any("Apptest Local (local)" in o for o in uni)
        assert any("Apptest Local (local)" in o for o in ref)
    finally:
        f.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# UI-FIX-1 — auto-persist run outputs, self-describing filenames, Scoreboard tab
# --------------------------------------------------------------------------- #
def _fixture_universe_result(universe_id="growth_40_v1"):
    from aristos_council.pipeline import RankPipelineResult
    from aristos_council.rank_engine import RankedTicker

    rt = RankedTicker(ticker="A", factor_ranks={"earnings_yield": 1.0},
                      factor_values={}, combined_rank=1.0, universe_size=1,
                      verdict="buy")
    return RankPipelineResult(
        ranked=[rt], excluded=[], unrateable=[], narratives={"A": "ranked #1."},
        header="Verdict: deterministic ranker.  Narrative: LLM (non-judging).",
        meta={"rank_strategy_id": "magic_formula_v1",
              "screen_strategy_id": "magic_value_screen_v1",
              "universe_id": universe_id, "council_mode": "narrator",
              "ranker_only": False, "universe_size": 1, "ranked_count": 1,
              "shortlist": ["A"], "est_cost": 0.05}, council_mode="narrator")


def test_persist_universe_run_writes_files_matching_the_download_bytes(monkeypatch,
                                                                        tmp_path):
    # A completed named-manifest run auto-persists .md + .html BEFORE any download
    # click — the fix for the two lost paid runs. Bytes must match what the download
    # buttons would serve (same builder functions), and the names must carry the
    # universe display-name slug (ITEM 2).
    from datetime import datetime, timezone

    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path / "universe_runs")
    result = _fixture_universe_result()
    run_start = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)

    md_path, html_path = app._persist_universe_run(result, run_start, "Growth 40")

    assert md_path.exists() and html_path.exists()
    assert md_path.parent == tmp_path / "universe_runs"
    assert "growth-40" in md_path.name and "growth-40" in html_path.name
    assert md_path.read_text(encoding="utf-8") == app._universe_markdown(result)

    from aristos_council.export.report_html import universe_report_html
    assert html_path.read_text(encoding="utf-8") == \
        universe_report_html(result, run_start=run_start)


def test_persist_universe_run_ad_hoc_run_persists_too(monkeypatch, tmp_path):
    # An ad-hoc run (Custom paste, or an Editor "Run once" with no Display name typed)
    # carries universe_display_name="" — SCOPE item 1 explicitly requires it persists
    # exactly like a named-manifest run, just without the slug segment.
    from datetime import datetime, timezone

    monkeypatch.setattr(app, "UNIVERSE_RUNS_DIR", tmp_path / "universe_runs")
    result = _fixture_universe_result(universe_id="adhoc:deadbeef")
    run_start = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)

    md_path, html_path = app._persist_universe_run(result, run_start, "")

    assert md_path.exists() and html_path.exists()
    assert md_path.name == "universe_magic_formula_v1_narrator_2026-08-05_1200.md"
    assert html_path.name == "universe_magic_formula_v1_narrator_2026-08-05_1200.html"


def test_saved_to_banner_shows_the_persisted_paths():
    # UI-FIX-1: the paths are printed prominently in the result header, before the rest
    # of the run renders — the first thing a user sees is where the run landed on disk.
    from datetime import datetime, timezone

    from streamlit.testing.v1 import AppTest

    md_p = app.ROOT / "reports" / "universe_runs" / "x.md"
    html_p = app.ROOT / "reports" / "universe_runs" / "x.html"
    at = AppTest.from_file(str(_APP), default_timeout=60)
    at.session_state["uni_results"] = [("magic_formula_v1", _fixture_universe_result())]
    at.session_state["uni_persisted"] = {"magic_formula_v1": (md_p, html_p)}
    at.session_state["uni_run_start"] = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)
    at.session_state["uni_universe_display_name"] = "My Portfolio"
    at = at.run()
    assert not at.exception
    blob = " ".join(str(getattr(s, "value", "")) for s in at.success)
    assert "Saved to:" in blob
    assert "reports" in blob and "universe_runs" in blob and "x.md" in blob
    assert "x.html" in blob


def test_each_strategy_result_renders_its_own_section_and_paths():
    # FUND-UI-2: several strategies over one list produce one result section each, with
    # per-strategy download keys and per-strategy persisted paths (no key collision, and
    # no section showing another run's files).
    from datetime import datetime, timezone

    from streamlit.testing.v1 import AppTest

    a = _fixture_universe_result()
    b = _fixture_universe_result()
    b.meta = dict(b.meta, rank_strategy_id="magic_formula_momentum_v1",
                  rank_strategy_name="Value + Momentum")
    at = AppTest.from_file(str(_APP), default_timeout=60)
    at.session_state["uni_results"] = [("magic_formula_v1", a),
                                       ("magic_formula_momentum_v1", b)]
    at.session_state["uni_persisted"] = {
        "magic_formula_v1": (app.ROOT / "reports" / "universe_runs" / "a.md",
                             app.ROOT / "reports" / "universe_runs" / "a.html"),
        "magic_formula_momentum_v1": (app.ROOT / "reports" / "universe_runs" / "b.md",
                                      app.ROOT / "reports" / "universe_runs" / "b.html")}
    at.session_state["uni_run_start"] = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    at = at.run()
    assert not at.exception
    blob = " ".join(str(getattr(s, "value", "")) for s in at.success)
    assert "a.md" in blob and "b.md" in blob
    md_blob = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "Value + Momentum" in md_blob                  # per-strategy section heading


def test_scoreboard_is_its_own_top_level_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert any(str(t.label) == "Scoreboard" for t in at.tabs)
    # The real committed snapshots/verdict_consensus.csv has rows, so the panel itself
    # (unchanged content — an expander with the persisted-snapshots table) renders.
    assert any("Persisted snapshots" in str(e.label) for e in at.expander)


def test_scoreboard_panel_is_no_longer_wired_into_universe_run():
    # Source-level regression guard: the panel moved OFF the Universe Run flow (ITEM 4).
    # AppTest can't distinguish "which tab" an element came from (all tab bodies execute
    # in one script run), so this pins the actual wiring instead.
    import inspect

    uni_src = inspect.getsource(app.render_universe_tab)
    assert "render_scoreboard_tab" not in uni_src
    assert "_render_snapshot_history" not in uni_src   # the old name is gone entirely
    assert not hasattr(app, "_render_snapshot_history")

    main_src = inspect.getsource(app.main)
    assert "render_scoreboard_tab" in main_src
    assert '"Scoreboard"' in main_src
