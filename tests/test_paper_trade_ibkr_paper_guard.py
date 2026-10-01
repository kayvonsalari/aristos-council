"""PAPER-TRADE-1 — the TEST-ISOLATION-1 guard covers ``PaperIBKR`` too.

Its own module, deliberately (same convention as ``test_gap_ledger_ibkr_guard.py``):
``test_paper_trade_ibkr_paper.py`` carries ``pytest.mark.real_adapter`` for the whole file,
and this one test has to run with the guard ACTIVE to prove it is there at all.
"""
from __future__ import annotations

import pytest

from aristos_council.paper_trade import ibkr_paper


def test_the_suite_refuses_to_construct_a_real_paper_ib_client():
    """A socket that can PLACE ORDERS is the last thing a test should open unguarded."""
    with pytest.raises(AssertionError) as caught:
        ibkr_paper.PaperIBKR()
    assert "inject a fake" in str(caught.value)
    assert "paper_trade.ibkr_paper.PaperIBKR" in str(caught.value)
