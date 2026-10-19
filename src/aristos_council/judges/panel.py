"""JUDGE-PANEL-1 - run the blind judge panel on one company and describe what came back.

This module makes NO decision. It asks each judge the same question twice, records what they said,
works out whether each judge was stable, counts the split and states whether the panel agrees with
the verdict of record. There is no average, no combined verdict and no adjudicator: the panel is a
forward test, and nothing it says reaches a lens, a rank, a badge, the agreement table or the
shortlist.

Per judge:
  not_run    the key is missing - the judge is shown as "not run - no key", the panel goes on
  stable     both calls gave the same verdict (ABSTAIN counts as a verdict); it is shown once
  unstable   both calls were valid and DIFFERED; both verdicts are kept
  invalid    a call returned malformed output twice (one retry each) - "invalid output"
  error      a call could not be made at all (network, HTTP status)
  incomplete one call valid and the other not - the valid verdict is shown, but it is not stable
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from .answer import VERDICTS, parse_answer
from .config import PanelConfig, JudgeSpec, load_config, prompt_text

NOT_RUN = "not_run"
STABLE = "stable"
UNSTABLE = "unstable"
INVALID = "invalid"
ERROR = "error"
INCOMPLETE = "incomplete"

AGREES = "agrees"
DISAGREES = "disagrees"
MIXED = "mixed"
NO_STABLE = "no_stable_judge"

NO_KEY_TEXT = "not run — no key"
RETRY_NOTE = ("\n\nYour previous reply was not a valid answer. Reply again with ONLY the single JSON "
              "object described above, with exactly its four keys and no other text.")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass
class JudgeCall:
    call_no: int
    status: str = "ok"                # ok | invalid | error
    verdict: str = ""
    confidence: str = ""
    reason: str = ""
    would_change_my_mind: str = ""
    attempts: int = 1
    error: str = ""


@dataclass
class JudgeResult:
    family: str
    label: str
    model: str
    status: str = NOT_RUN
    note: str = ""
    calls: list = field(default_factory=list)

    @property
    def verdicts(self) -> list[str]:
        return [c.verdict for c in self.calls if c.status == "ok" and c.verdict]

    @property
    def verdict(self) -> str:
        """The judge's verdict when it is stable, else ''."""
        return self.verdicts[0] if self.status == STABLE and self.verdicts else ""

    @property
    def is_stable(self) -> bool:
        return self.status == STABLE

    @property
    def first_ok(self) -> Optional[JudgeCall]:
        return next((c for c in self.calls if c.status == "ok"), None)

    def verdict_text(self) -> str:
        """What the page's verdict cell says."""
        if self.status == NOT_RUN:
            return NO_KEY_TEXT
        if self.status == STABLE:
            return "abstained" if self.verdict == "ABSTAIN" else self.verdict
        if self.status == UNSTABLE:
            return "unstable: " + " / ".join(self.verdicts)
        if self.status == INVALID:
            return "invalid output"
        if self.status == ERROR:
            return "error: " + (self.note or "the call failed")
        if self.status == INCOMPLETE:
            return f"incomplete: {self.verdicts[0]} on one call only"
        return self.status


@dataclass
class RecordSnapshot:
    """The verdict of record at the moment the panel ran: the lenses' own split, quoted."""
    verdict: str = ""                # BUY | HOLD | SELL | MIXED | "" (no vote)
    split: str = ""                  # "BUY 2 / HOLD 1 / SELL 0"
    headline: str = ""               # the agreement table's own sentence
    n_voted: int = 0


@dataclass
class PanelReading:
    """The narrator's one short paragraph about where and why the panel differs."""
    paragraph: str = ""
    note: str = ""                   # why there is no paragraph, when there is none
    calls: int = 0


@dataclass
class PanelResult:
    ticker: str = ""
    run_at: str = ""
    available: bool = False          # False: nothing was asked (see ``note``)
    note: str = ""
    pack_hash: str = ""
    pack_version: str = ""
    prompt_version: str = ""
    record: RecordSnapshot = field(default_factory=RecordSnapshot)
    judges: list = field(default_factory=list)
    agreement: str = NO_STABLE
    reading: Optional[PanelReading] = None
    calls_made: int = 0
    seconds: float = 0.0
    log_dir: str = ""            # where the rows were appended ("" when nothing was written)
    log_error: str = ""          # why they were not, when writing failed

    @property
    def split_line(self) -> str:
        return split_line(self.judges)

    @property
    def agreement_line(self) -> str:
        return agreement_line(self.agreement, self.record)


# --------------------------------------------------------------------------- #
# the verdict of record, as the panel sees it
# --------------------------------------------------------------------------- #
def record_snapshot(agreement) -> RecordSnapshot:
    """The lenses' split and - when ONE verdict holds a strict majority of the lenses that voted -
    that verdict. No strict majority is MIXED: the record itself is divided, and the panel is not
    asked to pick a side of it. (Same strict >50% rule as the MAJORITY_OVERRIDE veto.)"""
    if agreement is None or not getattr(agreement, "n_voted", 0):
        return RecordSnapshot()
    b, h, s = len(agreement.buy), len(agreement.hold), len(agreement.sell)
    n = b + h + s
    verdict = "MIXED"
    for word, count in (("BUY", b), ("HOLD", h), ("SELL", s)):
        if count * 2 > n:
            verdict = word
    return RecordSnapshot(verdict=verdict, split=f"BUY {b} / HOLD {h} / SELL {s}",
                          headline=agreement.headline, n_voted=n)


def split_line(judges) -> str:
    """"2 BUY, 1 ABSTAIN" - what the stable judges said, then the judges that did not settle."""
    counts = {w: 0 for w in VERDICTS}
    other = {UNSTABLE: 0, NOT_RUN: 0, INVALID: 0, ERROR: 0, INCOMPLETE: 0}
    for j in judges:
        if j.status == STABLE and j.verdict in counts:
            counts[j.verdict] += 1
        elif j.status in other:
            other[j.status] += 1
    parts = [f"{n} {w}" for w, n in counts.items() if n]
    for status, word in ((UNSTABLE, "unstable"), (INCOMPLETE, "incomplete"), (INVALID, "invalid output"),
                         (ERROR, "error"), (NOT_RUN, "not run")):
        if other[status]:
            parts.append(f"{other[status]} {word}")
    return ", ".join(parts) if parts else "no judge answered"


def stable_verdicts(judges) -> list[str]:
    """The stable judges' BUY/HOLD/SELL verdicts (an ABSTAIN takes no side)."""
    return [j.verdict for j in judges if j.status == STABLE and j.verdict in ("BUY", "HOLD", "SELL")]


def panel_agreement(judges, record: RecordSnapshot) -> str:
    """agrees | disagrees | mixed | no_stable_judge. No averaging: each stable judge is compared
    with the record on its own."""
    sides = stable_verdicts(judges)
    if not sides:
        return NO_STABLE
    if record.verdict in ("BUY", "HOLD", "SELL"):
        same = sum(1 for v in sides if v == record.verdict)
        if same == len(sides):
            return AGREES
        if same == 0:
            return DISAGREES
    return MIXED


def _record_phrase(record: RecordSnapshot) -> str:
    """The phrase 'the verdict of record (HOLD: BUY 0, HOLD 2, SELL 0)': the verdict word AND the
    lenses' own count, so the line never leaves a reader to translate "BUY on 0 of 2 votes" into a word."""
    if not record.verdict:
        return "the verdict of record"
    counts = record.split.replace(" / ", ", ")
    if record.verdict == "MIXED":
        return f"the verdict of record (the lenses are split: {counts})"
    return f"the verdict of record ({record.verdict}: {counts})"


def agreement_line(status: str, record: RecordSnapshot) -> str:
    rec = _record_phrase(record)
    if status == AGREES:
        return f"The panel agrees with {rec}."
    if status == DISAGREES:
        return f"The panel disagrees with {rec}."
    if status == MIXED:
        return f"The panel is mixed against {rec}."
    return "No judge gave a stable verdict, so there is nothing to compare with the verdict of record."


def needs_reading(status: str) -> bool:
    """The narrator is asked for a paragraph only when the panel differs from the record."""
    return status in (DISAGREES, MIXED)


# --------------------------------------------------------------------------- #
# running the judges
# --------------------------------------------------------------------------- #
def _ask_once(spec: JudgeSpec, system: str, user: str, caller, call_no: int) -> JudgeCall:
    """One call, retried ONCE if the reply is malformed. A failed network call is an error, not
    something a retry of the same words fixes, so it is not retried."""
    from .clients import CallError

    call = JudgeCall(call_no=call_no)
    why = ""
    for attempt in (1, 2):
        call.attempts = attempt
        try:
            text = caller(spec, system, user if attempt == 1 else user + RETRY_NOTE)
        except CallError as exc:
            call.status, call.error = "error", str(exc)[:300]
            return call
        except Exception as exc:                            # noqa: BLE001 - one judge must not stop the panel
            call.status, call.error = "error", f"{type(exc).__name__}: {str(exc)[:200]}"
            return call
        answer, why = parse_answer(text)
        if answer is not None:
            call.status = "ok"
            call.verdict, call.confidence = answer.verdict, answer.confidence
            call.reason, call.would_change_my_mind = answer.reason, answer.would_change_my_mind
            return call
    call.status, call.error = "invalid", why
    return call


def settle(spec: JudgeSpec, calls: list) -> JudgeResult:
    """The judge's status from its calls."""
    result = JudgeResult(family=spec.family, label=spec.label, model=spec.model, calls=calls)
    ok = [c for c in calls if c.status == "ok"]
    if len(ok) == len(calls) and calls:
        result.status = STABLE if len({c.verdict for c in ok}) == 1 else UNSTABLE
    elif not ok:
        if any(c.status == "error" for c in calls):
            result.status = ERROR
            result.note = next((c.error for c in calls if c.status == "error"), "")
        else:
            result.status = INVALID
            result.note = next((c.error for c in calls if c.status == "invalid"), "")
    else:
        result.status = INCOMPLETE
        result.note = next((c.error for c in calls if c.status != "ok"), "")
    return result


def run_judges(pack_text: str, config: PanelConfig, caller: Callable, *,
               environ=None) -> list:
    """Ask every judge with a key ``config.calls_per_judge`` times, in parallel. A judge with no
    key is returned as ``not_run`` and nothing is sent for it."""
    system = prompt_text()
    user = ("FACTS PACK\n\n" + pack_text +
            "\n\nAnswer now with the single JSON object described in your instructions.")
    results: list = [None] * len(config.judges)
    jobs = []
    for i, spec in enumerate(config.judges):
        if not spec.has_key(environ):
            results[i] = JudgeResult(family=spec.family, label=spec.label, model=spec.model,
                                     status=NOT_RUN, note="no key")
            continue
        for n in range(1, config.calls_per_judge + 1):
            jobs.append((i, n, spec))
    done: dict = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            futures = {(i, n): pool.submit(_ask_once, spec, system, user, caller, n)
                       for i, n, spec in jobs}
            done = {key: f.result() for key, f in futures.items()}
    for i, spec in enumerate(config.judges):
        if results[i] is None:
            results[i] = settle(spec, [done[(i, n)] for n in range(1, config.calls_per_judge + 1)])
    return results


def calls_made(judges) -> int:
    return sum(c.attempts for j in judges for c in j.calls)


def run_panel(report, *, fundamentals=None, config: Optional[PanelConfig] = None,
              caller: Optional[Callable] = None, environ=None, reader=None,
              strategies_dir=None, now: Optional[datetime] = None) -> PanelResult:
    """The whole panel for one ``CompanyReport``. Never raises.

    ``reader`` (optional) is a callable ``(result, report, pack) -> PanelReading`` standing in for
    the narrator, for tests; production uses ``narrate.write_panel_reading``. Nothing here touches
    ``report.agreement`` or any vote."""
    from .blind_pack import build_blind_pack
    from .config import PROMPT_VERSION

    started = time.perf_counter()
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    result = PanelResult(ticker=report.ticker, run_at=stamp, prompt_version=PROMPT_VERSION)
    try:
        if getattr(report, "unrateable", False):
            result.note = f"no data for this company ({report.check.data_integrity.note})"
            return result
        record = record_snapshot(report.agreement)
        if not record.verdict:
            # the same rule as the council opinion: with no lens verdict there is nothing to
            # compare with, so no judge is asked - and the page says so rather than going quiet
            result.note = "no lens gave a verdict of record for this company"
            return result
        result.record = record
        config = config or load_config()
        pack = build_blind_pack(report, fundamentals, strategies_dir=strategies_dir)
        result.pack_hash, result.pack_version = pack.pack_hash, pack.version
        if caller is None:
            from .clients import http_caller as caller
        result.judges = run_judges(pack.text(), config, caller, environ=environ)
        result.available = True
        result.agreement = panel_agreement(result.judges, record)
        result.calls_made = calls_made(result.judges)
        if needs_reading(result.agreement):
            if reader is None:
                from .narrate import write_panel_reading as reader
            result.reading = reader(result, report, pack)
            result.calls_made += getattr(result.reading, "calls", 0) or 0
    except Exception as exc:                                # noqa: BLE001 - reported, never raised
        result.available = False
        result.note = f"the judge panel failed: {type(exc).__name__}: {str(exc)[:200]}"
    result.seconds = round(time.perf_counter() - started, 1)
    return result
