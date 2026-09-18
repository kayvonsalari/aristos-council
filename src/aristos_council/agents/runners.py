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
    ARISTOS_MODEL_READER      (default: anthropic:claude-sonnet-4-6)

    ARISTOS_TEMP_SPECIALIST   (default: 0.0)
    ARISTOS_TEMP_CRITIC       (default: 0.0)
    ARISTOS_TEMP_DECISION     (default: 0.0)
    ARISTOS_TEMP_READER       (default: 0.0)

Temperature defaults to 0.0 on EVERY tier for reproducibility: Claude's own
default is 1.0 (maximum randomness), which made the verdict on a screen-passing,
near-boundary name wobble between runs. Temp 0.0 massively reduces that variance —
it does NOT make runs bit-identical (LLMs keep residual non-determinism even at 0),
so a verdict that still wobbles at 0.0 is genuinely borderline, which is signal.
"""

from __future__ import annotations

import json
import logging

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

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
    # READER-5 — the NARRATOR's tier (the same model the decision/narration stage runs
    # on), overridable as ever by ARISTOS_MODEL_READER.
    #
    # READER-1 put the reader on the cheapest tier, reasoning that nothing is being
    # reasoned about: the facts are already decided and the writer only arranges them.
    # That was right about the arithmetic and wrong about the job. Five live runs showed
    # what the job actually is — hold a whole run in view, describe each test by its role
    # without inferring one, name what every test agreed on, and say the hard part in
    # plain words for someone who does not work in finance. Four of those five summaries
    # were unpublishable. It is one call per RUN, not per name, so the stronger tier costs
    # a cent or two rather than a multiple of anything.
    "reader": "anthropic:claude-sonnet-4-6",
}

# Default temperature per tier. 0.0 everywhere — reproducibility first; the
# verdict (decision) in particular MUST be stable. Raise ONLY via env var if you
# later want a touch of specialist diversity.
_DEFAULT_TEMPS = {
    "specialist": 0.0,   # was implicitly 1.0 (Claude default)
    "critic": 0.0,
    "decision": 0.0,     # the verdict MUST be stable — temp 0
    "reader": 0.0,       # the same run must not produce a differently-worded summary
}


def _model_for(tier: str) -> str:
    """Resolve the model id for a tier (env override or default). Pure — no LLM."""
    return os.environ.get(f"ARISTOS_MODEL_{tier.upper()}", _DEFAULTS[tier])


def _temp_for(tier: str) -> float:
    """Resolve the temperature for a tier (env override or default). Pure — no LLM."""
    return float(os.environ.get(f"ARISTOS_TEMP_{tier.upper()}", _DEFAULT_TEMPS[tier]))


# --------------------------------------------------------------------------- #
# NARR-PARSE-1 — a nested object the model sent as a STRING
# --------------------------------------------------------------------------- #
# Live, 2026-09-18 (universe_my-portfolio_4lenses_ranker_2026-09-18_1619): the decision
# tier returned `narration` as a JSON STRING rather than as an object. The CONTENT was
# well formed; only the shape was wrong. Structured output rejected it, this runner
# re-raised, and one name took the whole narration stage down — the run produced no
# narration at all.
#
# The schema is right and stays right. This repairs the envelope ONCE, and only ever in
# the direction of "a string that is plainly JSON becomes the object it spells". A model
# that returns genuine nonsense still fails, exactly as today.
_MAX_REPAIR_DEPTH = 4


def repair_stringified(value, depth: int = 0) -> tuple[object, int]:
    """``(value, n_repaired)`` — nested objects sent as JSON strings, parsed in place.

    Walks dicts and lists so a stringified object nested one level down is reached too,
    but only ever converts a string that BEGINS as JSON punctuation and parses to a dict
    or list. A string field that happens to hold prose is left alone; so is a string
    holding a bare number, which would otherwise silently change a type.
    """
    if depth > _MAX_REPAIR_DEPTH:
        return value, 0
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
            except ValueError:
                return value, 0
            if isinstance(parsed, (dict, list)):
                deeper, extra = repair_stringified(parsed, depth + 1)
                return deeper, 1 + extra
        return value, 0
    if isinstance(value, dict):
        out, n = {}, 0
        for key, item in value.items():
            out[key], k = repair_stringified(item, depth + 1)
            n += k
        return out, n
    if isinstance(value, list):
        out_list, n = [], 0
        for item in value:
            fixed, k = repair_stringified(item, depth + 1)
            out_list.append(fixed)
            n += k
        return out_list, n
    return value, 0


def tool_call_args(raw) -> dict | None:
    """The dict the model actually produced, out of the raw AIMessage.

    ``with_structured_output(include_raw=True)`` keeps the message beside the parse
    failure, which is the only reason a repair is possible at all: the parsed value is
    None precisely when we need to look at what was sent.
    """
    for call in (getattr(raw, "tool_calls", None) or []):
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        if isinstance(args, dict):
            return args
    return None


class LangChainRunner:
    """Wraps init_chat_model(..., temperature=t).with_structured_output(schema).

    Imported lazily so the package (and the test suite) works without
    langchain-anthropic installed. ``model_id`` and ``temperature`` are kept as
    attributes so a run can RECORD which model + temperature produced the verdict
    (see ``runner_metadata`` / the report's ``models`` field).
    """

    def __init__(self, tier: str, schema: type[BaseModel], meter=None):
        from langchain.chat_models import init_chat_model  # lazy

        self.tier = tier
        self.schema = schema
        self.model_id = _model_for(tier)
        self.temperature = _temp_for(tier)
        # NARR-PARSE-1: how many stringified nested objects this runner has had to repair.
        # Counted rather than merely logged, because "it happens sometimes" is not a thing
        # anyone can act on and "it happened 4 times in this run" is.
        self.repaired = 0
        # COST-3: ONE meter shared across the tiers, because the bill for narrating a
        # name is the whole council pass for it, not the narrator's call alone.
        self.meter = meter
        # temperature is set on the BASE model BEFORE with_structured_output, so
        # the structured wrapper inherits it (init_chat_model forwards it to the
        # anthropic:* client).
        #
        # include_raw=True keeps the AIMessage alongside the parsed object so the
        # provider's own usage_metadata can be read. Without it the token counts are
        # discarded at the seam and the only cost figure available is the estimate. The
        # parsed value is unwrapped below, so this class's contract is unchanged:
        # invoke() still returns the schema instance, and a parse failure still raises.
        self._llm = init_chat_model(
            self.model_id, temperature=self.temperature
        ).with_structured_output(schema, include_raw=True)

    def invoke(self, system: str, user: str):
        out = self._llm.invoke([("system", system), ("user", user)])
        if not isinstance(out, dict):           # a provider that ignored include_raw
            return out
        parsed = out.get("parsed")
        if out.get("parsing_error"):
            # NARR-PARSE-1 — one repair attempt, then the original error. A model that
            # spelled a nested object as a JSON string said the right thing in the wrong
            # envelope; a model that said the wrong thing still fails here.
            parsed = self._repaired_parse(out.get("raw"))
            if parsed is None:
                raise out["parsing_error"]
        if self.meter is not None:
            raw = out.get("raw")
            self.meter.record(self.model_id, getattr(raw, "usage_metadata", None))
        return parsed

    def _repaired_parse(self, raw):
        """The answer with stringified nested objects parsed, or None to give up.

        Deliberately single-shot: repair, revalidate, done. A loop here would be a way to
        keep bending an answer until it fits, which is how a schema stops meaning anything.
        """
        args = tool_call_args(raw)
        if not isinstance(args, dict):
            return None
        fixed, n = repair_stringified(args)
        if not n:
            return None                          # nothing was stringified; a real failure
        try:
            validated = self.schema.model_validate(fixed)
        except Exception:
            return None                          # genuinely malformed — raise the original
        self.repaired += n
        logger.debug("%s: repaired %d stringified nested object(s) in the model's answer",
                     self.tier, n)
        return validated


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


def repaired_count(runners: dict) -> int:
    """How many stringified nested objects a runner set repaired (NARR-PARSE-1).

    Zero for test fakes, which have no counter — the same shape as ``cost_meter``, and for
    the same reason: a fake-runner run must report "nothing happened", not crash.
    """
    return sum(int(getattr(r, "repaired", 0) or 0) for r in runners.values())


def cost_meter(runners: dict):
    """The shared ``CostMeter`` behind a runner set, or ``None`` for test fakes.

    Callers use this to price ONE PHASE of a run (mark → work → since). A fake-runner
    run has no meter, so the actual cost reports as NOT MEASURED rather than as zero."""
    for r in runners.values():
        meter = getattr(r, "meter", None)
        if meter is not None:
            return meter
    return None


def production_runners() -> dict[str, "LangChainRunner"]:
    """Build the tiered runner set used by the real graph.

    All three tiers share ONE CostMeter (COST-3): what narrating a name costs is the
    whole council pass for it — specialists on the cheap tier, critic and narrator on the
    strong one — so a per-tier meter would only ever report a fraction of the bill."""
    from ..costs import CostMeter
    from .schemas import CriticOutput, DecisionOutput, SpecialistOutput

    meter = CostMeter()
    return {
        "specialist": LangChainRunner("specialist", SpecialistOutput, meter=meter),
        "critic": LangChainRunner("critic", CriticOutput, meter=meter),
        "decision": LangChainRunner("decision", DecisionOutput, meter=meter),
    }
