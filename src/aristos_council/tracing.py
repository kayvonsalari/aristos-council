"""Optional LangSmith tracing — env-gated, no-key no-op.

Mirrors the ``FINNHUB_API_KEY`` pattern exactly: tracing is OFF unless explicitly
switched on, and a missing key is a SILENT no-op (never a crash). LangSmith
AUTO-instruments LangChain when its standard env vars are present, so this module
does NOT send traces and does NOT touch the Runner/agent code. Its only jobs are
to (a) decide on/off honestly, (b) default the project, and (c) build per-run
trace METADATA (tags + metadata) for the graph invocation so the LangSmith UI is
filterable by ticker / strategy / provider / overrides.

Env vars (LangSmith's standard names):
    LANGSMITH_TRACING=true     master switch (absent / anything-else = off)
    LANGSMITH_API_KEY=<key>    required for tracing to actually send (secret)
    LANGSMITH_PROJECT=<name>   groups traces; defaults to "aristos-council"

The installed langsmith reads BOTH the LANGSMITH_* (preferred) and legacy
LANGCHAIN_* names (langsmith.utils.get_env_var, namespaces ("LANGSMITH",
"LANGCHAIN")), so setting the LANGSMITH_* names here is sufficient.
"""

from __future__ import annotations

import os

DEFAULT_PROJECT = "aristos-council"
_TRUTHY = {"true", "1", "yes", "on"}


def tracing_enabled() -> bool:
    """True only when the master switch is truthy AND an API key is present — so a
    switch flipped on without a key is silently OFF, never a half-configured crash."""
    return (os.environ.get("LANGSMITH_TRACING", "").strip().lower() in _TRUTHY
            and bool(os.environ.get("LANGSMITH_API_KEY")))


def tracing_project() -> str:
    """The LangSmith project traces group under (defaulted, never required)."""
    return os.environ.get("LANGSMITH_PROJECT") or DEFAULT_PROJECT


def trace_config(ticker: str, strategy_id: str, provider: str,
                 has_overrides: bool) -> dict:
    """RunnableConfig tags + metadata so a live run's trace is filterable in the
    LangSmith UI. Minimal and SECRET-FREE — no payloads, no keys. Harmless to pass
    when tracing is off (LangChain simply ignores it)."""
    return {
        "tags": [ticker, strategy_id, provider],
        "metadata": {
            "ticker": ticker,
            "strategy": strategy_id,
            "provider": provider,
            "overrides": has_overrides,
        },
    }


def status_line() -> str:
    """The honest on/off line to print at run start. SIDE EFFECT: when tracing is
    on, defaults ``LANGSMITH_PROJECT`` in-process so traces group consistently."""
    if tracing_enabled():
        project = tracing_project()
        os.environ["LANGSMITH_PROJECT"] = project
        return f"(tracing: LangSmith enabled — project {project})"
    return ("(tracing: off — set LANGSMITH_TRACING=true and LANGSMITH_API_KEY "
            "to enable)")
