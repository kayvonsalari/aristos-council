"""GAP-LEDGER-1 step 3b — the one place a model is allowed to speak, and its fence.

``--explain`` is OFF by default. When it is on, ONE call per run turns the headlines that
were already fetched into one plain-English line per candidate. That is the whole of the
model's job here.

What the model may not do, enforced structurally rather than asked for politely:

* **It never picks a name.** The candidate list is decided by ``screen.py`` before this
  module is imported, and this module cannot add to it or remove from it —
  ``explanations`` starts from the candidates it was handed and returns a line per handed
  candidate, dropping anything the model invented.
* **It never produces a number.** The gap and the relative volume are in the record from
  the deterministic screen. The prompt is given the headlines only, so there is no figure
  in scope for it to restate or recompute.
* **It never sees a name with no headlines.** Those are answered ``no clear reason found``
  without a call, which is both cheaper and the only honest answer: with nothing fetched
  there is nothing to summarize, and a model asked anyway would fill the silence.
* **Every line cites the link it came from**, so a claim can be checked against the story
  in one click.

Repo rule 1 ("agents never do arithmetic") is the ancestor of all four.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from pydantic import BaseModel, Field

from .news import Headline

_log = logging.getLogger(__name__)

# The mark for a name this step could not explain. One spelling, used by every path that
# declines: no headlines, a model that skipped the name, a model that never answered.
NO_REASON = "no clear reason found"

MAX_HEADLINES_PER_NAME = 4

SYSTEM_PROMPT = """\
You summarize pre-market news headlines. You are NOT an analyst and NOT a trader.

Rules:
- For each ticker, write ONE plain-English sentence saying what the headlines report.
- Use ONLY the headlines given. Never add context, numbers, prices, percentages,
  valuations, ratings or expectations from your own knowledge.
- End each sentence with the URL of the single headline it rests on, in parentheses.
- Never say whether to buy, sell or hold, and never say whether a move is justified,
  overdone or likely to continue.
- If the headlines for a ticker do not say anything that would explain a price move,
  return exactly "{no_reason}" as that ticker's line.
- Return a line for every ticker given, and for no other ticker."""


class _Line(BaseModel):
    ticker: str = Field(description="The ticker this line is about, exactly as given.")
    line: str = Field(description="One sentence, ending with the source URL in "
                                  "parentheses, or the no-reason marker.")


class _Lines(BaseModel):
    lines: list[_Line] = Field(default_factory=list,
                               description="One entry per ticker given, no others.")


# --------------------------------------------------------------------------- #
# the prompt — pure, so what the model is shown is testable without a model
# --------------------------------------------------------------------------- #
def build_prompt(news: dict[str, Sequence[Headline]], *,
                 max_headlines: int = MAX_HEADLINES_PER_NAME) -> str:
    """The headline block, and nothing else. No gap, no volume, no previous close."""
    blocks: list[str] = []
    for ticker in sorted(news):
        items = list(news[ticker])[:max_headlines]
        if not items:
            continue
        lines = [f"{ticker}:"]
        for item in items:
            when = item.published_ny or "time unknown"
            where = item.source or "source unknown"
            lines.append(f"  - [{when}] ({where}) {item.title}\n    {item.link}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class ExplainOutcome:
    """The lines, plus what happened. ``note`` is empty on a clean run."""

    lines: dict[str, str]
    called: bool = False
    note: str = ""


def explanations(candidates: Sequence[str], news: dict[str, Sequence[Headline]], *,
                 runner=None, max_headlines: int = MAX_HEADLINES_PER_NAME) -> ExplainOutcome:
    """One line per candidate. ``runner=None`` means ``--explain`` was off.

    A model failure degrades to ``NO_REASON`` for every name WITH A STATED NOTE, rather
    than taking the run down: the screen's numbers are the product, and the prose is a
    convenience. Silence would be the one unacceptable outcome — an absent line is
    indistinguishable from a line that was never asked for.
    """
    # GAP-NEWS-MATCH-1: with --explain OFF there is no reason line at all, and the header
    # says so once. ``NO_REASON`` is reserved for a run that ASKED and came back empty —
    # printing it per row on an off run implied a search that never happened.
    if runner is None:
        return ExplainOutcome({}, called=False, note="")
    out = {ticker: NO_REASON for ticker in candidates}
    with_news = {t: list(news.get(t) or []) for t in candidates}
    with_news = {t: items for t, items in with_news.items() if items}
    if not with_news:
        return ExplainOutcome(out, called=False,
                              note="no headlines for any candidate — no model call made")
    system = SYSTEM_PROMPT.format(no_reason=NO_REASON)
    try:
        answer = runner.invoke(system, build_prompt(with_news, max_headlines=max_headlines))
    except Exception as exc:                             # any provider or parse failure
        _log.warning("gap_ledger: explain call failed: %s", exc)
        return ExplainOutcome(out, called=True,
                              note=f"explanations unavailable — the model call failed ({exc})")
    for item in getattr(answer, "lines", None) or []:
        ticker = (getattr(item, "ticker", "") or "").strip().upper()
        text = (getattr(item, "line", "") or "").strip()
        # A ticker the model invented is discarded: it cannot add a name to the list.
        if ticker in out and text:
            out[ticker] = text
    return ExplainOutcome(out, called=True, note="")


def build_runner(meter=None):
    """The cheap tier, built lazily. Mirrors ``agents.runners`` so the model id, the
    temperature and the ``ARISTOS_MODEL_SPECIALIST`` override are the repo's, not a second
    set invented here."""
    from ..agents.runners import LangChainRunner

    return LangChainRunner("specialist", _Lines, meter=meter)
