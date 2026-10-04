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


def test_the_live_lenses_are_each_named_by_the_name_the_app_shows():
    """ARCH-DOC-2: the names come from the app's own picker (the visible rank strategies, in its
    order), not from a hand-kept list that can drift from it."""
    import sys

    sys.path.insert(0, str(DOC.parents[1]))
    from scripts.build_architecture_doc import _lens_name, _visible_lenses

    lenses = _visible_lenses()
    assert len(lenses) == 12                       # nine stock lenses and three ETF lenses
    text = _text()
    for lens in lenses:
        assert _lens_name(lens) in text, f"missing lens: {_lens_name(lens)!r}"


def test_the_lens_and_badge_tables_are_generated_so_they_cannot_drift():
    """ARCH-DOC-2: a lens's row is its own ``asks`` caption (Value + Momentum's 12% capital rule
    and Growth's three rules were missing from the hand-written version) and the badge table is
    ``backtest.BADGE_MEANINGS`` - the app's five labels, never the backtest's internal verdicts."""
    import html
    import subprocess
    import sys

    from aristos_council.backtest import BADGE_MEANINGS

    result = subprocess.run([sys.executable, "-m", "scripts.build_architecture_doc", "--check"],
                            cwd=DOC.parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    text = _text()
    for label, meaning in BADGE_MEANINGS.items():
        assert html.escape(label) in text and html.escape(meaning) in text
    for internal in ("not beyond luck", "not proven", ">insufficient<", ">proven<"):
        assert internal not in text, f"the backtest's own verdict leaked into the page: {internal}"
    assert "12% on their capital" in text           # Value + Momentum's rule
    assert "sales up at least 10% a year" in text   # Growth's rules


def test_the_page_states_what_each_feature_really_uses_for_news_and_for_click_through():
    text = _text()
    assert "EODHD news" in text and "Finnhub" in text          # the page company line AND council
    assert "without re-running" not in text
    assert "does not run anything" in text or "does not run" in text
    assert "own industry" in text and "not against your list" in text
    assert "This is a proposal" in text                          # the banner stays


def test_it_states_both_optional_model_features_are_never_a_verdict():
    text = _text().lower()
    assert "plain-english summary" in text and "council opinion" in text
    assert "ever changes, votes on, or overrides the verdict" in text
