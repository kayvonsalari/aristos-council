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


def test_parse_universe_normalizes_dedupes_and_orders():
    got = app._parse_universe("aapl, msft\nGOOGL aapl , ,brk.b")
    assert got == ["AAPL", "MSFT", "GOOGL", "BRK.B"]    # upper, de-duped, order kept
    assert app._parse_universe("") == []


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
    assert row["Verdict"] == "BUY" and row["#"] == 1
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
    assert "| 1 | A | BUY |" in md                      # the ranked row for A
    assert "## Excluded" in md and "min_roic" in md
    assert "## Unrateable" in md and "DEAD" in md
    assert "## Narrative" in md and "ranked #1 on ROIC." in md


def test_rank_dropdown_order_baseline_label_and_no_v2_heading():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    rank_dd = next(s for s in at.selectbox if s.label == "Rank strategy")
    opts = list(rank_dd.options)
    assert "momentum" in opts[0].lower()                        # flagship first
    # the baseline (magic_formula_v1) is HIDDEN by default now (ITEM 2) — it carries its
    # 'baseline — for comparison' label only under the validation toggle (asserted in
    # test_validation_assets_revealed_when_toggle_on).
    assert not any("baseline" in o.lower() for o in opts)
    heads = " ".join(str(getattr(e, "value", "")) for e in at.subheader)
    assert "Universe Run — screen, rank, verdict" in heads and "v2" not in heads


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


def test_universe_run_tab_renders_with_rank_dropdown():
    # The app renders (all tabs) with the new Universe Run tab present and a rank-
    # strategy dropdown — no run triggered, so nothing hits the network.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    # The "Rank strategy" selectbox only exists inside render_universe_tab, so its
    # presence proves the tab rendered.
    rank_dd = next(s for s in at.selectbox if s.label == "Rank strategy")
    # Options are the FRIENDLY display names now (ITEM 1) — the technical id is demoted
    # to a caption, so it never appears in the label (no ids/underscores/_v1).
    assert any("Value + Momentum" in o for o in rank_dd.options)
    assert not any("_" in o for o in rank_dd.options)


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
    # the v2 product IS the landing (the Universe Run rank dropdown renders)
    assert any(s.label == "Rank strategy" for s in at.selectbox)


def test_legacy_surfaces_appear_when_toggle_on():
    at = _legacy_app(60)
    assert not at.exception
    assert any("Run council" in b.label for b in at.button)          # council flow back
    assert "Run a council" in _header_blob(at)                       # legacy sidebar back
    assert any("Report · Legacy" in str(t.label) for t in at.tabs)   # legacy tabs back


def _dropdown(at, label):
    return next(s for s in at.selectbox if s.label == label)


def test_validation_assets_hidden_by_default(monkeypatch):
    # ITEM 2 + UNI-1: toggle OFF (default) -> universe dropdown = the two graded scoreboard
    # universes + the exploratory financials cohort (front-stage, role not 'never graded')
    # + Custom; strategy dropdown = the live strategies. The never-graded trap bench +
    # ui:hidden baseline stay hidden.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception

    uni = _dropdown(at, "Universe").options
    # substring (not startswith): the default strategy's suggested universe carries a ⭐
    # prefix (UNI-1 ITEM 2), so match the name irrespective of the group marker.
    assert any("Growth 40 · " in o for o in uni)
    assert any("Defensive Income 16 · " in o for o in uni)
    assert any("Financials 16 · " in o for o in uni)                 # UNI-1: front-stage
    assert "Custom (paste tickers)" in uni
    assert not any("Validation Bench" in o for o in uni)             # trap bench hidden
    assert not any("Energy Watch" in o for o in uni)                 # observation hidden
    assert any("Dividend ETFs (US)" in o for o in uni)               # ETF-1 exploratory cohort
    assert any("Growth ETFs (US)" in o for o in uni)                 # ETF-1 exploratory cohort
    assert any("Core Market ETFs (UCITS)" in o for o in uni)         # ETFCORE-1 cohort
    # 2 scoreboard + financials + 2 US ETF cohorts + 3 UCITS ETF cohorts (dividend +
    # growth [UCITS-1] + core [ETFCORE-1], all front-stage) + Custom = 9. (These app
    # tests skip in CI — streamlit is not in the dev extra — so the count had lagged the
    # UCITS-1 additions; corrected to the live front-stage set here.)
    assert len(uni) == 9

    rank = _dropdown(at, "Rank strategy").options
    assert not any("Classic Value" in o for o in rank)              # baseline hidden (ui: hidden)
    assert any("GARP" in o for o in rank)                           # growth is live (4C)
    assert any("RAW" in o for o in rank)                            # canonical raw (RAW-1)
    assert any("Financials" in o for o in rank)                     # financials lens (FIN-1)
    assert any("Dividend ETFs" in o for o in rank)                  # ETF-1 dividend lens
    assert any("Growth ETFs" in o for o in rank)                    # ETF-1 growth lens
    assert any("Core Market ETFs" in o for o in rank)               # ETFCORE-1 core lens
    # 5 stock lenses + 3 visible ETF lenses (dividend, growth, core [ETFCORE-1]).
    assert len(rank) == 8


def test_both_strategy_dropdowns_list_the_live_strategies():
    # 4C ITEM 2: the universe-run selector AND the Company Check selector both populate
    # from discovery with friendly display names; growth appears as GARP. RAW-1 makes it
    # four (the canonical no-screen variant is visible).
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    rank = _dropdown(at, "Rank strategy").options
    cc = _dropdown(at, "Strategy (lens screen + factors)").options
    for opts in (rank, cc):
        assert any("GARP" in o for o in opts)                        # growth as GARP
        assert any("RAW" in o for o in opts)                         # canonical raw
        assert any("Financials" in o for o in opts)                  # financials lens (FIN-1)
        assert not any("_" in o for o in opts)                       # display names, no ids
        # 5 stock lenses + 3 visible ETF lenses (dividend, growth, core [ETFCORE-1]).
        assert len(opts) == 8


def test_financials_16_is_front_stage_in_both_universe_selectors():
    # UNI-1 ITEM 1: financials_16 has no observational role, so it is FRONT-stage (toggle
    # off) in BOTH the Universe Run selector and the Company Check reference selector —
    # both discover from universes/ via the same role-derived visible_universes.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    uni = _dropdown(at, "Universe").options                          # Universe Run tab
    ref = _dropdown(at, "Reference universe (for factor context)").options  # Company Check
    assert any("Financials 16 · " in o for o in uni)                 # (⭐-prefix-robust)
    assert any("Financials 16 · " in o for o in ref)
    # the never-graded trap bench stays backstage in both (default toggle off)
    assert not any("Validation Bench" in o for o in uni)
    assert not any("Validation Bench" in o for o in ref)


def test_suggested_universe_renders_first_for_selected_strategy():
    # UNI-1 ITEM 2: select the financials lens -> its suggested universe (Financials 16)
    # heads the Universe dropdown with the ⭐ marker; every other universe stays
    # selectable below (a hierarchy, never a lock).
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    rank_dd = _dropdown(at, "Rank strategy")
    fin_label = next(o for o in rank_dd.options if "Financials" in o)
    rank_dd.set_value(fin_label).run()
    assert not at.exception
    uni = _dropdown(at, "Universe").options
    assert uni[0] == "⭐ Financials 16 · 16 names"                   # suggested group first
    assert not any(o.startswith("⭐") for o in uni[1:])             # only the suggested one
    assert any(o.startswith("Growth 40 ·") for o in uni)           # cross-lens still selectable


def test_validation_assets_revealed_when_toggle_on():
    at = _legacy_app(60)
    assert not at.exception
    uni = _dropdown(at, "Universe").options
    assert any("Validation Bench" in o for o in uni)                 # bench revealed
    rank = _dropdown(at, "Rank strategy").options
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
# Universe Editor (UNIED-1) — build/clone/run/save from the UI
# --------------------------------------------------------------------------- #
def test_universe_editor_section_renders():
    # The editor lives inside the Universe Run tab as its own expander; its "Start from"
    # clone selector + the two actions (Run once / Save) prove it rendered.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert any(s.label == "Start from" for s in at.selectbox)
    assert any("Universe Editor" in str(e.label) for e in at.expander)
    assert any("Run once" in b.label for b in at.button)
    assert any("Save to universes/local/" in b.label for b in at.button)


def test_custom_paste_adhoc_option_unchanged():
    # The existing ad-hoc Custom paste path is intact alongside the new editor — the
    # editor's Run once reuses the SAME path (universe_id=None), it does not replace it.
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    assert "Custom (paste tickers)" in _dropdown(at, "Universe").options


def test_saved_local_universe_appears_in_both_selectors():
    # UNIED-1 Item 3: a saved local universe is discovered front-stage (default toggle
    # off) in BOTH the Universe Run selector and the Company Check reference selector,
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
        uni = _dropdown(at, "Universe").options
        ref = _dropdown(at, "Reference universe (for factor context)").options
        assert any("Apptest Local (local)" in o for o in uni)
        assert any("Apptest Local (local)" in o for o in ref)
    finally:
        f.unlink(missing_ok=True)
