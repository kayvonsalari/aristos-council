"""GAP-IBKR-1 — the TEST-ISOLATION-1 guard covers the IB adapter.

Its own module, deliberately. ``test_gap_ledger_ibkr.py`` carries
``pytest.mark.real_adapter`` for the whole file — it exercises the real adapter class with an
injected IB handle, which is the documented opt-out (see docs/TESTING.md) — and this one test
has to run with the guard ACTIVE to prove it is there at all.
"""
from __future__ import annotations

import pytest

from aristos_council.gap_ledger import ibkr


def test_the_suite_refuses_to_construct_a_real_ib_client():
    """A socket to the owner's live broker is the last thing a test should open."""
    with pytest.raises(AssertionError) as caught:
        ibkr.IBKRBars()
    assert "inject a fake" in str(caught.value)
    assert "gap_ledger.ibkr.IBKRBars" in str(caught.value)
