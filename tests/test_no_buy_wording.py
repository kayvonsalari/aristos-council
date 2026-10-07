"""LIST-WORDING-1 - "1 name had no BUY from any lens, and are not listed here" (2026-10-06) -> the verb
agrees with the count."""
from types import SimpleNamespace

import pytest

from aristos_council import pipeline
from aristos_council.export.report_html import _shortlist_section


@pytest.mark.parametrize("n,expect", [
    (1, "1 name had no BUY from any lens, and is not listed here."),
    (2, "2 names had no BUY from any lens, and are not listed here."),
])
def test_the_no_buy_line_agrees_with_its_count(monkeypatch, n, expect):
    monkeypatch.setattr(pipeline, "lens_agreement_table", lambda ag: (["Name"], []))
    ag = SimpleNamespace(title="Shortlist", rule_sentence="rule", available=True,
                         overlap_note="", no_buy_count=n)
    assert expect in _shortlist_section(ag)
