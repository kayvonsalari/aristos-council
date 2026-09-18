"""TEST-ISOLATION-1 — no test may reach the real data adapter.

The scar: two narration tests built a real yfinance adapter because they were handed
none. Green here (yfinance is installed on the dev box), ``DataUnavailable: yfinance is
not installed`` in CI. Neither test was ABOUT the network; both had been quietly reaching
for it, and nothing said so until the CI box happened to lack the package.

So the suite now refuses. Every factory that can put a live provider in a test's hands is
replaced for the whole session by one that raises, naming the test that called it. A test
that needs data injects a fake; a test that genuinely exercises the provider says so out
loud with ``@pytest.mark.real_adapter`` and gets the real factory back.

This is a TEST-SIDE guard: no production module imports it and no production behaviour
changes. It cannot be defeated by a new entry point either — the guard is on the
factories themselves, not on their callers, so a future ``run_whatever`` that builds its
own adapter is caught the first time a test runs it.
"""
from __future__ import annotations

import os

import pytest

from aristos_council import pipeline as _pipeline
from aristos_council.data import provider as _provider
from aristos_council.data import sentiment as _sentiment

# --------------------------------------------------------------------------- #
# the message the brief asks for, plus the node id of whoever tripped it
# --------------------------------------------------------------------------- #
MESSAGE = "test reached the real data adapter; inject a fake"
MARKER = "real_adapter"

# Which test pytest is running, so the guard can name it. A dict rather than a fixture
# value because the guard is a plain function reached from arbitrary depth inside the
# code under test, with no fixture in scope.
_CURRENT: dict = {"nodeid": "<not in a test>", "opted_out": False}


def _guard(label: str, real):
    """``real``, but only for a test that asked for it in writing."""

    def _refuse(*args, **kwargs):
        if _CURRENT["opted_out"]:
            return real(*args, **kwargs)
        raise AssertionError(
            f"{MESSAGE}\n"
            f"  factory: {label}\n"
            f"  test:    {_CURRENT['nodeid']}\n"
            f"  Pass a fake adapter to the call under test, or — if this test is ABOUT "
            f"the provider — mark it @pytest.mark.{MARKER}."
        )

    _refuse.__wrapped__ = real          # so a test can still reach the real one
    return _refuse


def _sentiment_guard(real):
    """Sentiment is key-gated, so the guard is too.

    ``build_sentiment_adapter`` reaches Finnhub ONLY when ``FINNHUB_API_KEY`` is set;
    without one it returns ``(None, True, "")`` — an honest absence that half the pipeline
    tests exercise on purpose. Refusing that would break tests for a call that never left
    the machine. Refusing the OTHER branch is the point: a developer with a real key in
    their environment currently runs the suite against the live API without knowing it.
    """

    def _refuse(*args, **kwargs):
        if _CURRENT["opted_out"] or not (os.environ.get("FINNHUB_API_KEY") or "").strip():
            return real(*args, **kwargs)
        raise AssertionError(
            f"{MESSAGE}\n"
            f"  factory: data.sentiment.build_sentiment_adapter (FINNHUB_API_KEY is set)\n"
            f"  test:    {_CURRENT['nodeid']}\n"
            f"  Pass a fake sentiment adapter, unset the key, or mark the test "
            f"@pytest.mark.{MARKER}."
        )

    _refuse.__wrapped__ = real
    return _refuse


# Every factory a test can reach the network through. Found by reading every construction
# site of a live provider in ``src/`` and ``app.py``:
#
#   * ``data.provider.select_market_adapter``  — THE chokepoint. Every live market-data
#     adapter in the repo (yfinance, EODHD, hybrid) is built here and nowhere else, and
#     every caller imports it inside the function body, so patching the module attribute
#     catches app.py and the examples too.
#   * ``pipeline._build_adapter``             — the retry+cache wrapper around it. Already
#     covered by the line above, but guarded on its own so the failure names the pipeline
#     rather than a provider three frames down. (The brief calls this
#     ``data.adapter._build_adapter``; ``data/adapter.py`` holds the schema and the
#     ``MarketDataAdapter`` ABC, not a factory.)
#   * ``data.sentiment.build_sentiment_adapter`` — the Finnhub half, key-gated as above.
_TARGETS = (
    (_pipeline, "_build_adapter",
     lambda real: _guard("pipeline._build_adapter", real)),
    (_provider, "select_market_adapter",
     lambda real: _guard("data.provider.select_market_adapter", real)),
    (_sentiment, "build_sentiment_adapter", _sentiment_guard),
)


_INSTALLED: list = []


def _install() -> None:
    """Swap the real factories for the guards. Idempotent — never wraps a wrapper."""
    if _INSTALLED:
        return
    for module, attr, wrap in _TARGETS:
        real = getattr(module, attr)
        _INSTALLED.append((module, attr, real))
        setattr(module, attr, wrap(real))


def _restore() -> None:
    while _INSTALLED:
        module, attr, real = _INSTALLED.pop()
        setattr(module, attr, real)


def pytest_configure(config):
    """Install BEFORE collection, which is when test modules are imported.

    Patching a module attribute only reaches callers that look the name up at call time.
    Production code all does (every ``select_market_adapter`` import in ``src/`` and
    ``app.py`` is inside a function body), but four test modules import the factory at
    the top, and a name bound at import time keeps pointing at whatever it was bound to.
    Installing here, before those imports run, closes that hole.
    """
    del config
    _install()


@pytest.fixture(scope="session", autouse=True)
def no_real_adapter():
    """Hold the guards for the whole run, and put the real factories back after.

    ``pytest_configure`` has normally installed them already; this is the fixture the
    suite's isolation is DECLARED by, and the thing that guarantees the restore.
    """
    _install()
    try:
        yield
    finally:
        _restore()


@pytest.fixture(autouse=True)
def _current_test(request):
    """Tell the guards who is running, and whether they asked to be let through.

    A session-scoped fixture cannot see a per-test marker, so the opt-out is recorded
    here, one test at a time.

    The marker does NOT skip on its own. A ``real_adapter`` test that needs an optional
    dependency declares it the way the rest of the repo does — ``pytest.importorskip``
    (49 uses for streamlit, 2 for yfinance, one each for pandas/markdown/xhtml2pdf) — so
    that a provider test needing no yfinance (EODHD, or the unknown-name ValueError)
    still RUNS on a box without it instead of being skipped for nothing.
    """
    _CURRENT.update(nodeid=request.node.nodeid,
                    opted_out=request.node.get_closest_marker(MARKER) is not None)
    try:
        yield
    finally:
        _CURRENT.update(nodeid="<not in a test>", opted_out=False)
