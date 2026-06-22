"""The model seam: nodes depend on a tiny Runner protocol, not on LangChain.

Why: (a) unit tests inject FakeRunner objects and never touch a network or an
API key; (b) model tiering is a composition-root concern — specialists can run
on a cheap model and the Decision agent on a strong one without any node code
knowing about it.

Production runners are built via langchain's init_chat_model +
with_structured_output. Tiers are configured by env var so switching models (or
temperature) never requires a code change:

    ARISTOS_MODEL_SPECIALIST  (default: anthropic:claude-haiku-4-5)
    ARISTOS_MODEL_CRITIC      (default: anthropic:claude-sonnet-4-6)
    ARISTOS_MODEL_DECISION    (default: anthropic:claude-sonnet-4-6)

    ARISTOS_TEMP_SPECIALIST   (default: 0.0)
    ARISTOS_TEMP_CRITIC       (default: 0.0)
    ARISTOS_TEMP_DECISION     (default: 0.0)

Temperature defaults to 0.0 on EVERY tier for reproducibility: Claude's own
default is 1.0 (maximum randomness), which made the verdict on a screen-passing,
near-boundary name wobble between runs. Temp 0.0 massively reduces that variance —
it does NOT make runs bit-identical (LLMs keep residual non-determinism even at 0),
so a verdict that still wobbles at 0.0 is genuinely borderline, which is signal.
"""

from __future__ import annotations

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Runner(Protocol[T]):
    """A model behind a structured-output schema.

    `system` carries the STABLE content (role, hard rules, strategy rationale)
    — identical across runs, which models adhere to more reliably and which is
    eligible for provider-side prompt caching. `user` carries the PER-RUN
    content (ticker, evidence). Keep that split intact when implementing.
    """

    def invoke(self, system: str, user: str) -> T: ...


_DEFAULTS = {
    "specialist": "anthropic:claude-haiku-4-5",
    "critic": "anthropic:claude-sonnet-4-6",
    "decision": "anthropic:claude-sonnet-4-6",
}

# Default temperature per tier. 0.0 everywhere — reproducibility first; the
# verdict (decision) in particular MUST be stable. Raise ONLY via env var if you
# later want a touch of specialist diversity.
_DEFAULT_TEMPS = {
    "specialist": 0.0,   # was implicitly 1.0 (Claude default)
    "critic": 0.0,
    "decision": 0.0,     # the verdict MUST be stable — temp 0
}


def _model_for(tier: str) -> str:
    """Resolve the model id for a tier (env override or default). Pure — no LLM."""
    return os.environ.get(f"ARISTOS_MODEL_{tier.upper()}", _DEFAULTS[tier])


def _temp_for(tier: str) -> float:
    """Resolve the temperature for a tier (env override or default). Pure — no LLM."""
    return float(os.environ.get(f"ARISTOS_TEMP_{tier.upper()}", _DEFAULT_TEMPS[tier]))


class LangChainRunner:
    """Wraps init_chat_model(..., temperature=t).with_structured_output(schema).

    Imported lazily so the package (and the test suite) works without
    langchain-anthropic installed. ``model_id`` and ``temperature`` are kept as
    attributes so a run can RECORD which model + temperature produced the verdict
    (see ``runner_metadata`` / the report's ``models`` field).
    """

    def __init__(self, tier: str, schema: type[BaseModel]):
        from langchain.chat_models import init_chat_model  # lazy

        self.tier = tier
        self.model_id = _model_for(tier)
        self.temperature = _temp_for(tier)
        # temperature is set on the BASE model BEFORE with_structured_output, so
        # the structured wrapper inherits it (init_chat_model forwards it to the
        # anthropic:* client).
        self._llm = init_chat_model(
            self.model_id, temperature=self.temperature
        ).with_structured_output(schema)

    def invoke(self, system: str, user: str):
        return self._llm.invoke([("system", system), ("user", user)])


def runner_metadata(runners: dict) -> dict:
    """``{tier: {"model": id, "temperature": float}}`` for runners that expose it.

    Stamped on the run report so a verdict is auditable down to the model and
    temperature it ran at. Test fakes (no ``model_id``/``temperature``) are
    skipped, so this is harmless on a fake-runner run."""
    out: dict[str, dict] = {}
    for tier, r in runners.items():
        model = getattr(r, "model_id", None)
        temp = getattr(r, "temperature", None)
        if model is not None or temp is not None:
            out[tier] = {"model": model, "temperature": temp}
    return out


def production_runners() -> dict[str, "LangChainRunner"]:
    """Build the tiered runner set used by the real graph."""
    from .schemas import CriticOutput, DecisionOutput, SpecialistOutput

    return {
        "specialist": LangChainRunner("specialist", SpecialistOutput),
        "critic": LangChainRunner("critic", CriticOutput),
        "decision": LangChainRunner("decision", DecisionOutput),
    }
