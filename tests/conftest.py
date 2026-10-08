"""TEST-ISOLATION-1 — no test may reach the real data adapter.

The scar: two narration tests built a real yfinance adapter because they were handed
none. Green here (yfinance is installed on the dev box), ``DataUnavailable: yfinance is
not installed`` in CI. Neither test was ABOUT the network; both had been quietly reaching
for it, and nothing said so until the CI box happened to lack the package.

So the suite now refuses. Every factory that can put a live provider in a test's hands is
replaced for the whole session by one that raises, naming the test that called it. A test
that needs data injects a fake; a test that genuinely exercises the provider says so out
loud with ``@pytest.mark.real_adapter`` and gets the real factory back.

There are two ways in, and this file closes both. The first is a factory: production code
handed no adapter builds one. The second is a CREDENTIAL: ``tests/test_app.py`` drives the
Streamlit app through ``AppTest``, the app's ``main()`` calls ``load_dotenv(ROOT/".env")``
as it must, and from that moment the owner's real Finnhub, EODHD and Anthropic keys sit in
``os.environ`` for every test that follows. Nothing announced it; the suite simply behaved
differently on the owner's machine than in CI, where there is no ``.env``.

This is a TEST-SIDE guard: no production module imports it and no production behaviour
changes. It cannot be defeated by a new entry point either — the guard is on the
factories themselves, not on their callers, so a future ``run_whatever`` that builds its
own adapter is caught the first time a test runs it.
"""
from __future__ import annotations

import os

import pytest

from aristos_council import pipeline as _pipeline
from aristos_council.data import news_fallback as _news_fallback
from aristos_council.data import provider as _provider
from aristos_council.data import sentiment as _sentiment
from aristos_council.gap_ledger import bars as _gap_bars
from aristos_council.gap_ledger import ibkr as _gap_ibkr
from aristos_council.paper_trade import ibkr_paper as _paper_ibkr

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
#   * ``gap_ledger.bars.YFinanceBars``         — GAP-LEDGER-1. The pre-market screener does
#     not go through ``MarketDataAdapter`` (it needs 5-minute pre/post bars, which that
#     contract does not carry), so it is a SECOND way to a live provider and needs its own
#     guard. Constructing it is what is refused, exactly as for the adapters above: the
#     constructor is where the import of yfinance happens, and guarding construction is the
#     only place that catches a test that should not be constructing one at all.
#   * ``gap_ledger.ibkr.IBKRBars``          — GAP-IBKR-1, a socket to the owner's live IB
#     Gateway. Guarded for the same reason and more urgently.
#   * ``paper_trade.ibkr_paper.PaperIBKR``   — PAPER-TRADE-1, a socket to the owner's PAPER
#     Gateway that can PLACE ORDERS. Guarded on construction exactly like ``IBKRBars`` —
#     a test that wants the real class (even with a fake ``ib`` injected) opts in with
#     ``@pytest.mark.real_adapter``, same convention, no exceptions for "but it's paper".
#   * ``data.news_fallback.real_url_opener`` / ``real_yfinance_news`` — SENT-FALLBACK-1's
#     EODHD-news and yfinance-news fallback. Each fetcher takes an injectable
#     ``opener``/``fetcher`` and falls back to these TWO factories only when none is
#     given — which is every test's own call, except the many tests across the suite that
#     reach ``agents.nodes.gather()`` indirectly (through the rank pipeline, the council
#     graph, Company Check) WITHOUT configuring sentiment at all. Those used to abstain
#     for "no sentiment adapter" with no network reached; guarding here is what keeps that
#     true now that an unconfigured sentiment channel also tries these two as a fallback.
_TARGETS = (
    (_pipeline, "_build_adapter",
     lambda real: _guard("pipeline._build_adapter", real)),
    (_provider, "select_market_adapter",
     lambda real: _guard("data.provider.select_market_adapter", real)),
    (_sentiment, "build_sentiment_adapter", _sentiment_guard),
    (_gap_bars, "YFinanceBars",
     lambda real: _guard("gap_ledger.bars.YFinanceBars", real)),
    # GAP-IBKR-1. A THIRD route to live market data, and the one with a socket to the
    # owner's own broker on the other end. Read-only or not, no test may open it: the
    # guard is on construction, which is where the connection would be arranged.
    (_gap_ibkr, "IBKRBars",
     lambda real: _guard("gap_ledger.ibkr.IBKRBars", real)),
    (_paper_ibkr, "PaperIBKR",
     lambda real: _guard("paper_trade.ibkr_paper.PaperIBKR", real)),
    (_news_fallback, "real_url_opener",
     lambda real: _guard("data.news_fallback.real_url_opener", real)),
    (_news_fallback, "real_yfinance_news",
     lambda real: _guard("data.news_fallback.real_yfinance_news", real)),
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
    _silence_dotenv()


def _silence_dotenv() -> None:
    """``load_dotenv`` reads nothing for the duration of the suite.

    Scrubbing the environment around each test is not enough on its own: three tests drive
    the whole app through ``AppTest``, so ``main()`` loads the ``.env`` DURING the test and
    the pipeline reaches a live Finnhub client before any teardown can run. The fix has to
    be at the source — the suite does not read the developer's ``.env``, full stop.

    app.py is untouched and still loads it when actually run: the import is inside the
    function body, so it resolves this module attribute at call time. python-dotenv is a
    ``ui`` extra and legitimately absent on a ``.[dev]`` box, hence the guarded import.
    """
    try:
        import dotenv
    except ImportError:
        return
    _INSTALLED.append((dotenv, "load_dotenv", dotenv.load_dotenv))
    dotenv.load_dotenv = lambda *args, **kwargs: False


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
    _drop_credentials()


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


# --------------------------------------------------------------------------- #
# the second way in: credentials the suite never asked for
# --------------------------------------------------------------------------- #
# Not a hardening exercise — a fix for 27 observed failures. ``AppTest`` runs app.py,
# app.py loads the owner's ``.env``, and every later test that asks for a sentiment
# provider was handed a LIVE Finnhub client instead of the honest "no key" absence CI
# gets. ``test_sentiment_provider_status_is_logged`` asserts the no-key wording and was
# taking the other branch entirely.
#
# ANTHROPIC_API_KEY is on the list for a second reason: CLAUDE.md forbids it in this
# environment, and the suite was setting it anyway, half an hour into a test run.
_CREDENTIALS = ("ANTHROPIC_API_KEY", "FINNHUB_API_KEY", "EODHD_API_KEY")


def _drop_credentials() -> None:
    for name in _CREDENTIALS:
        os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def no_ambient_credentials():
    """No test reads the developer's keys — before it runs, or after it leaks them.

    Both ends matter. BEFORE, because a shell that exports a key would otherwise change
    what the suite tests; AFTER, because the test that loaded the ``.env`` is not the test
    that then goes to the network, and leaving the keys in place would make the result
    depend on which tests ran first.

    A test that wants a key present sets one with ``monkeypatch.setenv`` and gets exactly
    the value it chose — monkeypatch unwinds before this fixture does.
    """
    _drop_credentials()
    try:
        yield
    finally:
        _drop_credentials()


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


@pytest.fixture(autouse=True)
def _no_ambient_market_index_in_list_input(monkeypatch):
    """LIST-UI-1: the paste box's live resolver reads the local market index. A test must not
    depend on whether THIS machine has one (the owner's does, CI's does not), so the default
    lookup is empty (= "nothing to check against", the resolver abstains) unless a test sets its
    own with ``list_input._LOOKUP_CACHE`` or passes a lookup explicitly."""
    import aristos_council.list_input as li
    monkeypatch.setitem(li._LOOKUP_CACHE, "__default__", li.IndexLookup())
    yield


# --------------------------------------------------------------------------- #
# B25-1 - MIN-GROUP-5: a lens that ranks fewer than FIVE companies gives no verdict (it was three).
# --------------------------------------------------------------------------- #
# A great many fixtures here are 3-4 name universes built to exercise something ELSE (narration, grids,
# exports, counts) under the old three-name rule. Those modules say so in writing with
# ``pytestmark = pytest.mark.min_group(3)`` - the threshold they were designed under is pinned for the whole
# module - and every test ABOUT the five-name rule lives in tests/test_min_group_5.py, unpinned.
import sys as _sys


def _pin_everywhere(pinned: int):
    saved = []
    for name, mod in list(_sys.modules.items()):
        if mod is not None and (name.startswith("aristos_council") or name == "app")                 and isinstance(getattr(mod, "MIN_RANKABLE_COHORT", None), int):
            saved.append((mod, mod.MIN_RANKABLE_COHORT))
            mod.MIN_RANKABLE_COHORT = pinned
    return saved


def _unpin(saved):
    for mod, value in saved:
        mod.MIN_RANKABLE_COHORT = value


@pytest.fixture(scope="module", autouse=True)
def _pinned_min_group_module(request):
    """A module-level ``pytestmark = pytest.mark.min_group(3)`` also covers its MODULE-scoped fixtures."""
    raw = getattr(request.module, "pytestmark", None) or []
    raw = raw if isinstance(raw, (list, tuple)) else [raw]
    marks = [m for m in raw if getattr(m, "name", "") == "min_group"]
    if not marks:
        yield
        return
    saved = _pin_everywhere(int(marks[-1].args[0]))
    try:
        yield
    finally:
        _unpin(saved)


@pytest.fixture(autouse=True)
def _pinned_min_group(request):
    marker = request.node.get_closest_marker("min_group")
    if marker is None:
        yield
        return
    saved = _pin_everywhere(int(marker.args[0]))
    try:
        yield
    finally:
        _unpin(saved)


# --------------------------------------------------------------------------- #
# B25-3 - tie_rule(name): pin rank_engine.TIE_VERDICT_RULE for a test written under the old alphabetical
# tie-break (goldens and disclosure tests). Tests ABOUT the new rule live in tests/test_tie_best_position.py.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _pinned_tie_rule(request):
    marker = request.node.get_closest_marker("tie_rule")
    if marker is None:
        yield
        return
    from aristos_council import rank_engine
    saved = rank_engine.TIE_VERDICT_RULE
    rank_engine.TIE_VERDICT_RULE = str(marker.args[0])
    try:
        yield
    finally:
        rank_engine.TIE_VERDICT_RULE = saved
