"""The guard in conftest.py, tested like anything else.

A safety net nobody checks is a safety net that quietly stops catching. These four tests
pin the two halves of the contract — it refuses by default, it stands aside when a test
asks in writing — and they do it WITHOUT a provider installed: ``select_market_adapter``
is asked for a name it does not know, so the real function's own ``ValueError`` proves the
call reached it and the guard's ``AssertionError`` proves it did not.
"""
from __future__ import annotations

import pytest

from aristos_council.data.provider import select_market_adapter

from tests.conftest import MESSAGE


def test_building_a_real_adapter_is_refused_and_the_message_names_this_test():
    with pytest.raises(AssertionError) as exc:
        select_market_adapter("bloomberg")
    text = str(exc.value)
    assert MESSAGE in text
    assert "test_building_a_real_adapter_is_refused_and_the_message_names_this_test" in text
    assert "real_adapter" in text          # the message says how to opt out


def test_the_pipelines_own_factory_is_refused_too():
    """Guarded separately from the provider so the failure names the PIPELINE.

    ``_build_adapter`` is what a pipeline entry point calls when a caller hands it no
    adapter — the exact shape of the incident this file exists for.
    """
    from datetime import date

    from aristos_council import pipeline

    with pytest.raises(AssertionError, match=MESSAGE):
        pipeline._build_adapter(today=date(2026, 1, 1), use_cache=False)


@pytest.mark.real_adapter
def test_the_marker_hands_the_real_factory_back():
    """The real function is reached: its OWN error comes out, not the guard's."""
    with pytest.raises(ValueError, match="unknown ARISTOS_MARKET_PROVIDER"):
        select_market_adapter("bloomberg")


def test_the_guard_is_back_after_a_marked_test():
    """The opt-out is per test. If it leaked, the next test would be unprotected."""
    with pytest.raises(AssertionError, match=MESSAGE):
        select_market_adapter("bloomberg")
