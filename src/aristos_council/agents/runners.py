"""The model seam: nodes depend on a tiny Runner protocol, not on LangChain.

Why: (a) unit tests inject FakeRunner objects and never touch a network or an
API key; (b) model tiering is a composition-root concern — specialists can run
on a cheap model and the Decision agent on a strong one without any node code
knowing about it.

Production runners are built via langchain's init_chat_model +
with_structured_output. Tiers are configured by env var so switching models
never requires a code change:

    ARISTOS_MODEL_SPECIALIST  (default: anthropic:claude-haiku-4-5)
    ARISTOS_MODEL_CRITIC      (default: anthropic:claude-sonnet-4-6)
    ARISTOS_MODEL_DECISION    (default: anthropic:claude-sonnet-4-6)
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


class LangChainRunner:
    """Wraps init_chat_model(...).with_structured_output(schema).

    Imported lazily so the package (and the test suite) works without
    langchain-anthropic installed.
    """

    def __init__(self, tier: str, schema: type[BaseModel]):
        from langchain.chat_models import init_chat_model  # lazy

        model_id = os.environ.get(
            f"ARISTOS_MODEL_{tier.upper()}", _DEFAULTS[tier]
        )
        self._llm = init_chat_model(model_id).with_structured_output(schema)

    def invoke(self, system: str, user: str):
        return self._llm.invoke([("system", system), ("user", user)])


def production_runners() -> dict[str, "LangChainRunner"]:
    """Build the tiered runner set used by the real graph."""
    from .schemas import CriticOutput, DecisionOutput, SpecialistOutput

    return {
        "specialist": LangChainRunner("specialist", SpecialistOutput),
        "critic": LangChainRunner("critic", CriticOutput),
        "decision": LangChainRunner("decision", DecisionOutput),
    }
