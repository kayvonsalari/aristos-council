"""ARCH-DOC-1 (BATCH 16 item 7) — the one-page architecture overview for non-technical
testers (docs/aristos-architecture.html) is self-contained and describes TODAY's app,
never a feature that has since been removed or renamed.

Not a content-accuracy checker (that needs a human reading today's app — the hand test
B16-T8) — this pins the mechanical contract: no external request, no stray unclosed
tag, and none of the three removed features TAB-MERGE-1 took out named by their old
wording, so a later rewrite of this doc can't silently regress them back in.
"""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "aristos-architecture.html"

# TAB-MERGE-1 (parts 1 and 2) removed these three; a doc describing TODAY's app must
# never describe any of them as if they still existed.
_REMOVED_FEATURES = (
    "Company Check tab",
    "separate Company Check",
    "include-small tick box",
    "include small tick box",
    "valuation band tick box",
)


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_doc_exists_and_is_non_trivial():
    assert DOC.exists(), f"expected {DOC} to exist"
    assert len(_text()) > 2000


def test_the_doc_is_self_contained_no_external_request():
    text = _text()
    assert "http://" not in text and "https://" not in text
    assert "<link" not in text
    assert "<script" not in text


def test_the_doc_never_mentions_a_removed_feature():
    text = _text()
    for phrase in _REMOVED_FEATURES:
        assert phrase.lower() not in text.lower(), f"found removed feature: {phrase!r}"


def test_every_common_tag_is_balanced():
    text = _text()
    for tag in ("div", "table", "tr", "td", "th", "ul", "li", "p", "h2", "h4"):
        opens = len(re.findall(rf"<{tag}[ >]", text))
        closes = len(re.findall(rf"</{tag}>", text))
        assert opens == closes, f"<{tag}>: {opens} open vs {closes} close"


def test_the_doctrine_and_every_required_section_is_present():
    text = _text()
    for heading in (
        "math judges, LLM writes",
        "The lenses",
        "Forensic",
        "valuation band",
        "Analyse tab",
        "Track-record badges",
        "council and the plain-English summary",
        "Gap Ledger",
        "Where the numbers come from",
    ):
        assert heading.lower() in text.lower(), f"missing section: {heading!r}"


def test_the_live_lenses_are_each_named():
    text = _text()
    for lens in ("Defensive Income", "Cyclical Income", "Value + Momentum",
                "Growth (GARP)", "Magic Formula RAW", "Financials", "Forensic",
                "Dividend ETFs", "Growth ETFs", "ETF Core"):
        assert lens in text, f"missing lens: {lens!r}"


def test_it_states_both_optional_model_features_are_never_a_verdict():
    text = _text().lower()
    assert "plain-english summary" in text and "council opinion" in text
    assert "ever changes, votes on, or overrides the verdict" in text
