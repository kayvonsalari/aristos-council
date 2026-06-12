"""Tests for the purpose-built PDF export (export/report_pdf.py).

The PDF deps (xhtml2pdf, markdown) live in the optional ``ui`` extra, so this
module skips cleanly when they're absent. Covers the HTML builder (sections,
provenance cleaning, status colors) and that real PDF bytes are produced — both
for a constructed report and the saved JNJ/MO/BRK-B records.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone

import pytest

pytest.importorskip("markdown")
pytest.importorskip("xhtml2pdf")

from aristos_council.export.report_pdf import (  # noqa: E402
    render_report_pdf,
    report_to_html,
)
from aristos_council.persistence.reports import RunReport, load_report  # noqa: E402
from aristos_council.state import (  # noqa: E402
    CriticReport,
    Decision,
    Figure,
    Provenance,
    Recommendation,
    SpecialistName,
    SpecialistOpinion,
    Stance,
)


def _report() -> RunReport:
    return RunReport(
        ticker="MO",
        run_at=datetime(2026, 6, 12, 12, 40, tzinfo=timezone.utc),
        strategy_id="dividend_aristocrats_v1",
        company_name="Altria Group",
        screen={"criteria": [
            {"name": "min_dividend_yield", "passed": True, "observed": 0.059,
             "threshold": 0.025},
            {"name": "max_payout_ratio", "passed": False, "observed": 0.88,
             "threshold": 0.75},
            {"name": "min_market_cap", "passed": None, "observed": None,
             "threshold": 1e10},
        ]},
        specialist_opinions=[SpecialistOpinion(
            specialist=SpecialistName.FUNDAMENTAL, stance=Stance.BEARISH,
            confidence=0.6,
            thesis="Payout stretched (call_id: abc123, criteria[1].passed = false).",
            figures=[Figure(label="payout", value=0.88, unit="ratio",
                            provenance=Provenance(
                                tool_name="run_dividend_aristocrat_screen",
                                call_id="abc123",
                                field_path="criteria[1].observed"))],
            caveats=["streak is a floor"],
        )],
        critic_report=CriticReport(
            targets_stance=Stance.BEARISH, counter_thesis="Yield is rich.",
            weaknesses_found=["thin coverage"], open_questions=["FCF cover?"]),
        decision=Decision(recommendation=Recommendation.HOLD, confidence=0.52,
                          rationale="**Summary:** hold for $119.2B reasons.",
                          dissent=[SpecialistName.FUNDAMENTAL]),
        provenance_audit={"figures_audited": 9, "verified": 7, "mismatch": 1,
                          "unresolvable": 1, "unverifiable": 0, "unit_scaled": 0,
                          "violations": ["a mismatch"], "unit_scaled_notes": []},
    )


def test_report_to_html_has_every_section():
    h = report_to_html(_report())
    assert "MO" in h and "Altria Group" in h            # identity header
    assert "Screen results" in h and "min_dividend_yield" in h
    assert "Decision rationale" in h
    assert "Specialists" in h and "Fundamental" in h
    assert "Critic" in h
    assert "Provenance audit" in h
    # footer: strategy id + page-number tags for xhtml2pdf
    assert "dividend_aristocrats_v1" in h
    assert "<pdf:pagenumber" in h and "<pdf:pagecount" in h


def test_html_strips_callid_plumbing_but_keeps_figure_field():
    h = report_to_html(_report())
    assert "call_id" not in h            # prose cleaned for the record
    assert "abc123" not in h             # the id itself is gone
    # the deterministic figures table still carries the source field
    assert "criteria[1].observed" in h
    # currency survives (no $-eating; this is HTML, not st.markdown)
    assert "$119.2B" in h


def test_status_colors_present_in_html():
    h = report_to_html(_report())
    assert "#2E7D32" in h and "#B23B3B" in h and "#B8860B" in h  # PASS/FAIL/N-E


def test_render_report_pdf_produces_pdf_bytes():
    pdf = render_report_pdf(_report())
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1500


@pytest.mark.parametrize("ticker", ["JNJ", "MO", "BRK-B"])
def test_pdf_from_each_saved_report(ticker):
    path = sorted(glob.glob(f"reports/{ticker}/*.json"))[-1]
    pdf = render_report_pdf(load_report(path))
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1500
