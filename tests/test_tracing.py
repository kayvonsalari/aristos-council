"""Optional LangSmith tracing — env-gated on/off logic + trace metadata.

Tracing itself is external (LangChain auto-instrumentation); these tests only pin
the env-var DECISION and the metadata dict. No real key, no network — and the
status logic mirrors the FINNHUB no-key-no-op pattern.
"""

from __future__ import annotations

from aristos_council.tracing import (
    DEFAULT_PROJECT,
    status_line,
    trace_config,
    tracing_enabled,
    tracing_project,
)


def _clear(monkeypatch):
    for k in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.delenv(k, raising=False)


# --- on/off decision ------------------------------------------------------- #
def test_disabled_when_no_env(monkeypatch):
    _clear(monkeypatch)
    assert tracing_enabled() is False


def test_disabled_when_switch_on_but_no_key(monkeypatch):
    # the FINNHUB pattern: a switch with no key is silently OFF, never a crash
    _clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert tracing_enabled() is False


def test_disabled_when_key_but_switch_off(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-secret")
    assert tracing_enabled() is False          # switch absent
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert tracing_enabled() is False          # switch explicitly off


def test_enabled_when_switch_truthy_and_key_present(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-secret")
    for val in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("LANGSMITH_TRACING", val)
        assert tracing_enabled() is True, val


# --- project default ------------------------------------------------------- #
def test_project_defaults_then_honors_override(monkeypatch):
    _clear(monkeypatch)
    assert tracing_project() == DEFAULT_PROJECT == "aristos-council"
    monkeypatch.setenv("LANGSMITH_PROJECT", "my-eval-run")
    assert tracing_project() == "my-eval-run"


# --- status line + side effect --------------------------------------------- #
def test_status_line_off(monkeypatch):
    _clear(monkeypatch)
    line = status_line()
    assert "off" in line and "LANGSMITH_TRACING=true" in line


def test_status_line_enabled_defaults_project_in_process(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-secret")
    line = status_line()
    assert "LangSmith enabled" in line and "aristos-council" in line
    # side effect: project is defaulted in-process so traces group consistently
    import os
    assert os.environ["LANGSMITH_PROJECT"] == "aristos-council"


# --- trace metadata dict --------------------------------------------------- #
def test_trace_config_builds_expected_keys():
    cfg = trace_config("KO", "dividend_aristocrats_v1", "hybrid", True)
    assert cfg["tags"] == ["KO", "dividend_aristocrats_v1", "hybrid"]
    assert cfg["metadata"] == {
        "ticker": "KO", "strategy": "dividend_aristocrats_v1",
        "provider": "hybrid", "overrides": True,
    }
    # no secrets / payloads leak into the metadata
    flat = str(cfg).lower()
    assert "api_key" not in flat and "langsmith" not in flat


def test_trace_config_baseline_has_overrides_false():
    cfg = trace_config("JNJ", "dividend_aristocrats_v1", "yfinance", False)
    assert cfg["metadata"]["overrides"] is False
