"""Self-contained HTML export of the universe report + Company Check (REPORT-HTML-1).

The HTML is a PRESENTATION layer over the same report data; the ``.md`` universe report and
the ``.txt`` Company Check stay canonical. So these tests pin four things:

1. **No content silently dropped** — every narration sentence and every ``[⚠ narration
   check: …]`` stamp the markdown carries is in the HTML.
2. **Self-contained** — one file, inline CSS, no ``http(s)://`` reference, no script, no
   external asset of any kind.
3. **The canonical outputs are untouched** — the md/txt renderers produce the same bytes
   before and after an HTML export (the export never mutates the result).
4. **Print** — an ``@media print`` block with A4 pages and a page break per name section.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import pytest

from aristos_council.company_check import (
    CompanyCheckResult,
    DataIntegrity,
    FactorCell,
    GateCell,
    ScreenCell,
    format_company_check,
)
from aristos_council.export.report_html import (
    DISCLAIMER,
    DOCTRINE,
    company_check_html,
    universe_report_html,
)
from aristos_council.narration_check import _sentences
from aristos_council.pipeline import RankPipelineResult
from aristos_council.rank_engine import RankedTicker

# 2026-07-09 15:30 UTC -> 17:30 Europe/Berlin (CEST) — the same stamp convention the
# download filenames use.
_RUN = datetime(2026, 7, 9, 15, 30, tzinfo=timezone.utc)

_TAG = re.compile(r"<[^>]+>")
_MARKS = re.compile(r"[*`]+")
_LEAD_MARK = re.compile(r"^\s*[-•#]+\s*")


def _squash(text: str) -> str:
    """A CONTENT comparison key: markdown emphasis markers dropped (the export consumes
    them), a leading list/heading marker dropped, then ALL whitespace removed — so a tag
    boundary inside a sentence can never break the match."""
    return re.sub(r"\s+", "", _LEAD_MARK.sub("", _MARKS.sub("", text)))


def _visible(doc: str) -> str:
    """The document's visible text as a ``_squash`` key (inline CSS dropped first, tags
    replaced by whitespace, entities unescaped)."""
    body = doc.split("</style>", 1)[-1]
    return _squash(html.unescape(_TAG.sub("\n", body)))


def _universe_md(result) -> str:
    """The CANONICAL markdown download. It lives in app.py, which imports streamlit (the
    optional ``ui`` extra), so the md-comparison assertions skip cleanly without it — the
    HTML assertions themselves never need Streamlit."""
    pytest.importorskip("streamlit")
    import app
    return app._universe_markdown(result)


# --------------------------------------------------------------------------- #
# Fixtures — a three-name ETF run with a boundary tie, an abstention, a provenance
# receipt in the prose, and both a MATCHED and an UNMATCHED narration-check stamp.
# --------------------------------------------------------------------------- #
_MATCHED_STAMP = ('[⚠ narration check: "Momentum is the weakest leg at rank 3 of 3" '
                  "contradicts rank table — table is authoritative]")
_UNMATCHED_STAMP = ('[⚠ narration check: "a claim nowhere in the prose" contradicts '
                    "rank table — table is authoritative]")

_NARRATIVE_A = (
    "**SXR8.DE** takes the best combined rank-sum in this cohort.\n"
    "- Cost: expense_ratio 0.07 [static: 2026-07-21, EODHD] — the cheapest leg.\n"
    "- Size: fund_size is rank 1 of 3.\n"
    "\n"
    "Momentum is the weakest leg at rank 3 of 3.\n"
    + _MATCHED_STAMP
)
_NARRATIVE_B = (
    "EUNL.DE is tied on the combined rank-sum and the verdict split alphabetically.\n"
    + _UNMATCHED_STAMP
)


def _ranked() -> list[RankedTicker]:
    a = RankedTicker(
        ticker="SXR8.DE", factor_ranks={"expense_ratio": 1.0, "fund_size": 1.0},
        factor_values={"expense_ratio": 0.07, "fund_size": 8.0e10},
        combined_rank=2.0, universe_size=3, verdict="buy",
        factor_sources={"expense_ratio": "static: 2026-07-21, EODHD",
                        "fund_size": "computed"})
    b = RankedTicker(
        ticker="EUNL.DE", factor_ranks={"expense_ratio": 2.0, "fund_size": 3.0},
        factor_values={"expense_ratio": 0.20, "fund_size": 5.0e10},
        combined_rank=5.0, universe_size=3, verdict="hold",
        factor_sources={"expense_ratio": "static: 2026-07-21, EODHD",
                        "fund_size": "computed"})
    c = RankedTicker(
        ticker="VWCE.DE", factor_ranks={"expense_ratio": 3.0, "fund_size": 2.0},
        factor_values={"expense_ratio": 0.22, "fund_size": None},
        combined_rank=5.0, universe_size=3, verdict="sell",
        imputed_factors=["fund_size"],
        screen_abstentions={"max_payout_ratio_fcf": "not evaluated: mean FCF "
                                                    "non-positive"},
        factor_sources={"expense_ratio": "static: 2026-07-21, EODHD",
                        "fund_size": "computed"})
    return [a, b, c]


def _universe_result() -> RankPipelineResult:
    return RankPipelineResult(
        ranked=_ranked(),
        excluded=[("IWDA.AS", "asset kind 'ETF' outside this strategy's scope")],
        unrateable=[("DEAD.DE", "UNRATEABLE: no data — possibly delisted")],
        narratives={"SXR8.DE": _NARRATIVE_A, "EUNL.DE": _NARRATIVE_B},
        header="Verdict: deterministic ranker.  Narrative: LLM (non-judging).",
        meta={"rank_strategy_id": "etf_core_v1",
              "rank_strategy_name": "ETF Index Tracker",
              "screen_strategy_id": "none",
              "universe_id": "etf_core_5_v1",
              "council_mode": "narrator", "ranker_only": False,
              "universe_size": 5, "ranked_count": 3, "shortlist": ["SXR8.DE"],
              "narrate_coverage": "buys_only", "est_cost": 0.19},
        council_mode="narrator",
        fetch_errors=[("SPY5.DE", "fetch error: 429 after 3 retries")],
        screen_bases={"EUNL.DE": {"max_payout_ratio_fcf": "fcf"}},
        names={"SXR8.DE": "iShares Core S&P 500", "EUNL.DE": "iShares Core MSCI World"})


def _company_result(**over) -> CompanyCheckResult:
    base = dict(
        ticker="MU", company_name="Micron Technology",
        rank_strategy_id="magic_formula_momentum_v1",
        screen_strategy_id="magic_value_screen_v1",
        reference_universe_id="growth_40_v1", unrateable=False,
        screen=[
            ScreenCell(name="min_roic", observed=0.08, threshold=0.12, status="FAIL",
                       gating=True, borderline=True),
            ScreenCell(name="max_peg_ratio", observed=None, threshold=1.5, status="FAIL",
                       note="growth non-positive — fails closed by design"),
        ],
        gates=[GateCell(name="min_market_cap", status="PASS", detail="1.2e11 vs 1e10",
                        rationale="the floor keeps micro caps out of the cohort")],
        factors=[
            FactorCell(factor="expense_ratio", label="Expense ratio", value=0.07,
                       source="static: 2026-07-21, EODHD",
                       context="#2 of 5 in growth_40_v1"),
            FactorCell(factor="momentum_12m", label="12-month momentum", value=0.42,
                       source="computed", context="#1 of 5 in growth_40_v1"),
        ],
        divergence_flag="price +42% while the fundamental floor failed",
        reference_available=True, reference_run_id="20260721_growth_40_v1",
        reference_run_date="2026-07-21", reference_cohort_n=5,
        data_integrity=DataIntegrity(
            fundamentals_ok=True, price_ok=True,
            abstained_criteria=["max_payout_ratio_fcf"],
            not_evaluated_factors=["net_payout_yield"],
            implausible=["dividend_yield 0.2393 (>15%) — vendor value implausible "
                         "— flagged"]),
        pointer="A rank/verdict is a cohort statement — run the universe to place MU.",
        verdict_of_record="in the latest frozen run of growth_40_v1 (run 2026-07-21): "
                          "SELL, rank 12 of 16.",
    )
    base.update(over)
    return CompanyCheckResult(**base)


# --------------------------------------------------------------------------- #
# 1 — nothing silently dropped
# --------------------------------------------------------------------------- #
def test_universe_html_keeps_every_narration_sentence_in_the_md():
    result = _universe_result()
    md = _universe_md(result)
    doc = universe_report_html(result, run_start=_RUN)
    visible = _visible(doc)
    for narrative in result.narratives.values():
        for sentence in _sentences(narrative):
            assert _squash(sentence) in _squash(md)          # present in the canonical md
            assert _squash(sentence) in visible              # …and in the HTML


def test_universe_html_keeps_every_narration_check_stamp_as_a_callout():
    result = _universe_result()
    doc = universe_report_html(result, run_start=_RUN)
    stamps = [line for n in result.narratives.values()
              for line in n.splitlines() if "⚠" in line]
    assert len(stamps) == 2
    for stamp in stamps:
        # verbatim (escaped) in the document…
        assert html.escape(stamp, quote=False) in doc
        # …inside a warning callout, not loose in the prose
        assert '<div class="callout stamp">' in doc
    # The MATCHED stamp is attached to the paragraph stating the sentence it quotes; the
    # UNMATCHED one is never dropped — it lands at the end of that name's section.
    claim = "Momentum is the weakest leg at rank 3 of 3."
    assert doc.index(claim) < doc.index(html.escape(_MATCHED_STAMP, quote=False))
    assert html.escape(_UNMATCHED_STAMP, quote=False) in doc


def test_universe_html_renders_positions_boundary_ties_and_integrity():
    doc = universe_report_html(_universe_result(), run_start=_RUN)
    visible = _visible(doc)
    # "#N of M · score (best/worst)" positions, ties SHARING a position (RANK-DISPLAY-1)
    assert _squash("#1 of 3 · score 2 (best 2 · worst 6)") in visible
    assert _squash("#2 of 3 (tied) · score 5 (best 2 · worst 6)") in visible
    # boundary-tie flag on BOTH sides of the split (VERDICT-TIE-1)
    assert _squash("⚑ boundary (tied 5 with VWCE.DE — SELL") in visible
    assert _squash("⚑ boundary (tied 5 with EUNL.DE — HOLD") in visible
    # the abstention footnote (†) and the factor-integrity + screen-basis blocks
    assert _squash("† VWCE.DE — screen criterion not evaluated") in visible
    assert "Factor integrity" in doc and _squash("static: 2026-07-21, EODHD") in visible
    assert "Screen basis" in doc and _squash("FCF (4y mean) 1/1") in visible
    # the three NON-verdict axes stay distinct
    assert "Excluded" in doc and "IWDA.AS" in doc
    assert "Unrateable" in doc and "DEAD.DE" in doc
    assert "Fetch failed" in doc and "SPY5.DE" in doc


def test_provenance_receipts_render_as_badges():
    doc = universe_report_html(_universe_result(), run_start=_RUN)
    # brackets kept INSIDE the badge, so the text stays verbatim against the md
    assert '<span class="badge">[static: 2026-07-21, EODHD]</span>' in doc


def test_universe_html_header_and_footer_carry_the_shareable_facts():
    doc = universe_report_html(_universe_result(), run_start=_RUN)
    assert "ETF Index Tracker" in doc                     # strategy display name
    assert "etf_core_v1" in doc                           # strategy id
    assert "etf_core_5_v1" in doc                          # universe id
    assert "09.07.2026 17:30" in doc                       # run timestamp (Europe/Berlin)
    assert "narrator" in doc                               # mode
    assert DOCTRINE in doc                                 # house doctrine line
    assert DISCLAIMER in doc                               # research tool, not advice
    assert "not financial advice" in DISCLAIMER


def test_universe_html_display_name_falls_back_to_the_id_and_never_invents():
    result = _universe_result()
    result.meta.pop("rank_strategy_name")
    doc = universe_report_html(result)                      # no run_start either
    assert "<h1>etf_core_v1</h1>" in doc
    assert "ETF Index Tracker" not in doc
    # A run timestamp that was never supplied is OMITTED, not guessed.
    assert "09.07.2026" not in doc


# --------------------------------------------------------------------------- #
# 2 — self-contained (+ print stylesheet)
# --------------------------------------------------------------------------- #
def _assert_self_contained(doc: str) -> None:
    lowered = doc.lower()
    assert doc.startswith("<!DOCTYPE html>")
    assert "http://" not in lowered and "https://" not in lowered
    assert "<script" not in lowered and "<link" not in lowered
    assert "<img" not in lowered and " src=" not in lowered and "url(" not in lowered
    assert "<style>" in doc                                # all CSS inline, one file


def test_universe_html_is_self_contained():
    _assert_self_contained(universe_report_html(_universe_result(), run_start=_RUN))


def test_company_check_html_is_self_contained():
    _assert_self_contained(company_check_html(_company_result(), run_start=_RUN))


def test_print_stylesheet_paginates_and_stays_grayscale_legible():
    for doc in (universe_report_html(_universe_result(), run_start=_RUN),
                company_check_html(_company_result(), run_start=_RUN)):
        assert "@media print" in doc
        assert "@page { size: A4 portrait" in doc
        assert "display: table-header-group" in doc         # tables repeat their head
    doc = universe_report_html(_universe_result(), run_start=_RUN)
    assert "break-before: page" in doc                      # one name per page
    assert "page-break-before: always" in doc               # legacy alias
    # Collapsible sections are rendered OPEN so a browser Print never drops their content.
    assert '<details class="name-section" open>' in doc


# --------------------------------------------------------------------------- #
# 3 — the canonical .md / .txt outputs are untouched
# --------------------------------------------------------------------------- #
def test_html_export_leaves_the_canonical_markdown_byte_identical():
    result = _universe_result()
    before = _universe_md(result)
    narratives = dict(result.narratives)
    universe_report_html(result, run_start=_RUN)
    assert _universe_md(result) == before                     # same bytes
    assert result.narratives == narratives                   # result never mutated
    assert "<div" not in before and "<style" not in before   # md stays markdown


def test_html_export_leaves_the_canonical_company_check_text_byte_identical():
    result = _company_result()
    before = format_company_check(result)
    company_check_html(result, run_start=_RUN)
    assert format_company_check(result) == before
    assert "<div" not in before and "<style" not in before


# --------------------------------------------------------------------------- #
# 3b — VALBAND-2: the valuation band in the HTML, from the SAME source as the md
# --------------------------------------------------------------------------- #
from aristos_council.pipeline import valuation_band_rows   # noqa: E402

_BAND_HEADING = "Valuation band (absolute — vs each name's own history)"


def _universe_result_with_bands() -> RankPipelineResult:
    """The band-ON fixture: the same three-name run, with a valuation band set on each
    ranked ticker — two computed, one ABSTAINING (a recent-IPO reason that must stay
    visible, per VALBAND-1's honesty rule)."""
    result = _universe_result()
    bands = {
        "SXR8.DE": "78th percentile of own 5-year EV/EBIT band (band from 55/60 months)",
        "EUNL.DE": "23rd percentile of own 5-year P/E band (fallback) (band from 41/60 "
                   "months)",
        "VWCE.DE": "not evaluated — insufficient history: 1.4y",
    }
    for r in result.ranked:
        r.valuation_band = bands[r.ticker]
    return result


def test_universe_html_renders_the_band_matching_valuation_band_rows_exactly():
    result = _universe_result_with_bands()
    doc = universe_report_html(result, run_start=_RUN)
    visible = _visible(doc)

    assert _squash(_BAND_HEADING) in visible                  # same heading as the md
    rows = valuation_band_rows(result)
    assert len(rows) == 3                                     # one row per rateable name
    # every row present, VERBATIM (name — band, the shared "**name** — band" shape), in the
    # SAME ORDER as the source.
    keys = [_squash(f"{name} — {band}") for name, band in rows]
    for k in keys:
        assert k in visible
    positions = [visible.index(k) for k in keys]
    assert positions == sorted(positions)


def test_universe_html_band_and_markdown_cannot_drift():
    result = _universe_result_with_bands()
    doc = _visible(universe_report_html(result, run_start=_RUN))
    md = _squash(_universe_md(result))                        # importorskip streamlit
    # the SAME rows appear in both surfaces — the anti-drift guarantee of one shared source.
    for name, band in valuation_band_rows(result):
        key = _squash(f"{name} — {band}")
        assert key in doc and key in md


def test_universe_html_keeps_an_abstaining_bands_reason_intact():
    result = _universe_result_with_bands()
    visible = _visible(universe_report_html(result, run_start=_RUN))
    assert _squash("VWCE.DE — not evaluated — insufficient history: 1.4y") in visible


def test_universe_html_renders_the_section_when_every_band_is_a_failure_abstention():
    # VALBAND silent-failure fix: when the band was REQUESTED but every name's fetch
    # FAILED, each string is "not evaluated — price history unavailable: …" (never "—"),
    # so valuation_band_rows does NOT drop the section (it drops only when every band is
    # "—", i.e. never requested). The report shows the reason, not silence.
    result = _universe_result()
    for r in result.ranked:
        r.valuation_band = ("not evaluated — price history unavailable: "
                            "RuntimeError: no timezone found")
    rows = valuation_band_rows(result)
    assert len(rows) == 3                                     # section NOT dropped
    visible = _visible(universe_report_html(result, run_start=_RUN))
    assert _squash(_BAND_HEADING) in visible
    for name, band in rows:
        assert _squash(f"{name} — {band}") in visible


def test_universe_html_places_the_band_after_the_ranked_table_before_exclusions():
    doc = universe_report_html(_universe_result_with_bands(), run_start=_RUN)
    i_ranked = doc.index("Ranked — verdict of record")
    i_band = doc.index("Valuation band (absolute")
    i_excluded = doc.index("Excluded — screen")
    assert i_ranked < i_band < i_excluded                     # mirrors the markdown order


def test_universe_html_with_band_off_renders_no_band_section():
    # the default fixture sets no valuation_band -> valuation_band_rows is empty -> the
    # HTML must carry NO band heading (a band-off run's HTML is unchanged from before
    # VALBAND-2). The other universe-HTML tests all run on this same band-off fixture and
    # must keep passing, which is the byte-stability guard.
    result = _universe_result()
    assert valuation_band_rows(result) == []
    doc = universe_report_html(result, run_start=_RUN)
    assert _BAND_HEADING not in doc
    assert "Valuation band" not in doc


# --------------------------------------------------------------------------- #
# 4 — Company Check
# --------------------------------------------------------------------------- #
def test_company_check_html_renders_the_whole_diagnostic():
    result = _company_result()
    doc = company_check_html(result, run_start=_RUN,
                             strategy_display_name="Value + Momentum (flagship)")
    visible = _visible(doc)
    assert "Micron Technology (MU)" in doc                   # ticker + display name
    assert "magic_formula_momentum_v1" in doc                # strategy id
    assert "Value + Momentum (flagship)" in doc              # friendly name
    assert "growth_40_v1" in doc                             # reference universe
    assert "09.07.2026 17:30" in doc                         # run timestamp
    assert "NO VERDICT" in doc                               # never issues one at n=1
    # screen: every criterion with its three-valued status and its tags
    assert "min_roic" in doc and "FAIL" in doc and "borderline" in doc
    assert _squash("fails closed by design") in visible or "fails closed" in doc
    # gates + rationale
    assert "min_market_cap" in doc and "keeps micro caps out" in doc
    # factors: value, source badge, cohort context
    assert '<span class="badge">[static: 2026-07-21, EODHD]</span>' in doc
    assert _squash("#2 of 5 in growth_40_v1") in visible
    # the verdict OF RECORD is quoted, never recomputed
    assert "VERDICT OF RECORD" in doc and "SELL, rank 12 of 16" in doc
    # divergence + the ⚠ data flags as callouts
    assert "divergence" in doc.lower() and "price +42%" in doc
    assert _squash("⚠ dividend_yield 0.2393 (>15%)") in visible
    assert '<div class="callout alert">' in doc
    assert "max_payout_ratio_fcf" in doc                     # abstained criteria
    assert "net_payout_yield" in doc                         # not-evaluated factors
    assert result.pointer in doc
    assert DOCTRINE in doc and DISCLAIMER in doc


def test_company_check_html_unrateable_says_so_and_stops():
    result = _company_result(
        unrateable=True, screen=[], gates=[], factors=[], divergence_flag=None,
        verdict_of_record=None,
        data_integrity=DataIntegrity(fundamentals_ok=False, price_ok=False,
                                     note="no fundamentals and no price history"))
    doc = company_check_html(result, run_start=_RUN)
    assert "UNRATEABLE" in doc and "no fundamentals and no price history" in doc
    assert "Factor values" not in doc                         # no diagnosis at all
    assert DOCTRINE in doc                                    # footer still travels
    _assert_self_contained(doc)


def test_company_check_html_screenless_strategy_says_it_screens_nothing():
    doc = company_check_html(_company_result(screen=[], screen_less=True),
                             run_start=_RUN)
    assert "No lens screen" in doc and "quality enters via ranking" in doc
