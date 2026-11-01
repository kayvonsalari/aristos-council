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
NO_READING = "no_panel_reading"
NO_RECORD = "no_record"          # no lens voted: the panel is shown, there is nothing to compare with
NO_VOTE_TEXT = "no lens vote"    # what the verdict-of-record column of the logs reads then
MIN_JUDGES_ANSWERED = 2          # a panel is only read when at least this many judges were stable

NO_KEY_TEXT = "not run — no key"
RETRY_NOTE = ("\n\nYour previous reply was not a valid answer. Reply again with ONLY the single JSON "
              "object described above, with exactly its seven keys, no other text, and every claim "
              "in driving_fact and reasoning ending with [pack] or [own knowledge].")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass
class JudgeCall:
    call_no: int
    status: str = "ok"                # ok | invalid | error
    verdict: str = ""
    confidence: str = ""
    reason: str = ""                  # the judge's reasoning, claims labelled [pack] / [own knowledge]
    driving_fact: str = ""
    key_risk: str = ""
    unexplained: str = ""
    would_change_my_mind: str = ""
    own_knowledge_claims: int = 0
    attempts: int = 1
    error: str = ""                   # short label for a failed call ("no credits", "404"...)
    error_detail: str = ""            # the RAW error body: logged, never shown


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

    @property
    def shown_calls(self) -> list:
        """The answers the page shows for this judge: the first valid one, or - an unstable judge - both."""
        ok = [c for c in self.calls if c.status == "ok"]
        if self.status == UNSTABLE:
            return ok
        return ok[:1] if self.status in (STABLE, INCOMPLETE) else []

    @property
    def figures_off(self) -> int:
        """JUDGE-FIGURE-CHECK-1: sentences marked "[figure not in the pack]" in the answers shown. Counted from
        the marks in the text, not stored, so the saved results record keeps its exact shape and a reloaded
        panel states the same count."""
        from .figures import count_marks
        return sum(count_marks(c) for c in self.shown_calls)

    def verdict_text(self) -> str:
        """What the page's verdict cell says."""
        if self.status == NOT_RUN:
            return NO_KEY_TEXT
        if self.status == STABLE:
            return "abstained" if self.verdict == "ABSTAIN" else self.verdict
        if self.status == UNSTABLE:
            return "unstable: " + " / ".join(self.verdicts)
        if self.status == INVALID:
            return "error: invalid output"
        if self.status == ERROR:
            return "error: " + (self.note or "failed")
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

    @property
    def cell(self) -> str:
        """What the logs' verdict-of-record column holds: the verdict, or "no lens vote"."""
        return self.verdict or NO_VOTE_TEXT


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
    peers: list = field(default_factory=list)   # the lens universe minus the company (the scorer's cohort)

    @property
    def split_line(self) -> str:
        return split_line(self.judges)

    @property
    def figures_off(self) -> int:
        """JUDGE-FIGURE-CHECK-1: sentences, across the answers the page shows, that quote a figure the pack
        does not hold."""
        return sum(j.figures_off for j in self.judges)

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


def judges_answered(judges) -> int:
    """How many JUDGES (not calls) gave a stable verdict, an abstention included: a judge that
    errored, was unstable, was incomplete or had no key did not answer."""
    return sum(1 for j in judges if j.status == STABLE)


def panel_agreement(judges, record: RecordSnapshot) -> str:
    """agrees | disagrees | mixed | no_stable_judge | no_panel_reading. No averaging: each stable
    judge is compared with the record on its own, and only when at least two judges answered -
    one judge's answer is not a panel."""
    if judges_answered(judges) < MIN_JUDGES_ANSWERED:
        return NO_READING
    sides = stable_verdicts(judges)
    if not sides:
        return NO_STABLE
    if record.verdict not in ("BUY", "HOLD", "SELL", "MIXED"):
        return NO_RECORD
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
    if status == NO_RECORD:
        return "There is no verdict of record to compare with: no lens voted."
    if status == NO_READING:
        return "No panel reading: fewer than two judges answered."
    return "The judges that answered all abstained, so there is nothing to compare with the verdict of record."


def needs_reading(status: str) -> bool:
    """The narrator is asked for a paragraph only when the panel differs from the record."""
    return status in (DISAGREES, MIXED)


# --------------------------------------------------------------------------- #
# running the judges
# --------------------------------------------------------------------------- #
# GEMINI-RETRY-2: a busy server (503), a rate limit (429) or a timeout is retried ONCE, after a short
# pause, for every judge family - Gemini answered 503 on 5 of 7 pages in the 2026-10-22 sweep.
TRANSIENT_STATUSES = (429, 503)
TRANSIENT_BACKOFF_SECONDS = 2.0


def is_transient(exc) -> bool:
    """A failure a second try a moment later can fix: a timeout, HTTP 503 or HTTP 429 - but not a
    429 that says the account is out of credits, which no wait cures."""
    from .clients import error_label

    label = error_label(exc)
    if label == "no credits":
        return False
    return bool(getattr(exc, "timeout", False)) or label == "timeout" or \
        getattr(exc, "status", None) in TRANSIENT_STATUSES


def _ask_once(spec: JudgeSpec, system: str, user: str, caller, call_no: int, *,
              sleep: Optional[Callable] = None) -> JudgeCall:
    """One call, retried ONCE if the reply is malformed and ONCE (after a short pause) if the call
    failed transiently - timeout, HTTP 503, HTTP 429. A second transient failure is recorded as the
    error ("error: 503", the raw detail kept for the log). Any other failed call (400, 404, no
    credits...) is an error, not something a retry of the same words fixes, so it is not retried.
    ``sleep`` is the test seam for the pause."""
    from .clients import CallError, error_label

    call = JudgeCall(call_no=call_no, attempts=0)
    why = ""
    raw = ""
    malformed = False                 # a bad reply has been seen: the next ask carries the retry note
    retried = ""                      # the transient failure already retried (raw detail), "" if none
    while True:
        call.attempts += 1
        try:
            text = caller(spec, system, user + RETRY_NOTE + f" The problem was: {why}." if malformed else user)
        except CallError as exc:
            if is_transient(exc) and not retried:
                retried = str(exc) or error_label(exc)
                (sleep or time.sleep)(TRANSIENT_BACKOFF_SECONDS)
                continue
            call.status, call.error, call.error_detail = "error", error_label(exc), str(exc)
            return call
        except Exception as exc:                            # noqa: BLE001 - one judge must not stop the panel
            call.status, call.error, call.error_detail = "error", "failed", f"{type(exc).__name__}: {exc}"
            return call
        answer, why = parse_answer(text)
        raw = text
        if answer is not None:
            call.status = "ok"
            call.verdict, call.confidence = answer.verdict, answer.confidence
            call.reason, call.would_change_my_mind = answer.reasoning, answer.would_change_my_mind
            call.driving_fact, call.key_risk = answer.driving_fact, answer.key_risk
            call.unexplained = answer.unexplained
            call.own_knowledge_claims = answer.own_knowledge_claims
            if retried:               # the log keeps what was recovered from; the page never shows it
                call.error_detail = f"recovered on retry after: {retried}"
            return call
        if malformed:
            break
        malformed = True
    # the log keeps the rejected reply itself (cut), so a format error can be diagnosed afterwards
    call.status, call.error, call.error_detail = "invalid", "invalid output", f"{why} | raw: {(raw or '')[:1500]}"
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
               environ=None, sleep: Optional[Callable] = None) -> list:
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
            futures = {(i, n): pool.submit(_ask_once, spec, system, user, caller, n, sleep=sleep)
                       for i, n, spec in jobs}
            done = {key: f.result() for key, f in futures.items()}
    for i, spec in enumerate(config.judges):
        if results[i] is None:
            results[i] = settle(spec, [done[(i, n)] for n in range(1, config.calls_per_judge + 1)])
    # JUDGE-FIGURE-CHECK-1: mark any sentence that quotes a figure the pack does not hold. No verdict moves.
    from .figures import check_judges
    check_judges(results, pack_text)
    return results


def calls_made(judges) -> int:
    return sum(c.attempts for j in judges for c in j.calls)


def run_panel(report, *, fundamentals=None, config: Optional[PanelConfig] = None,
              caller: Optional[Callable] = None, environ=None, reader=None,
              strategies_dir=None, now: Optional[datetime] = None,
              statements=None, news=None) -> PanelResult:
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
        # JUDGE-PANEL-3: the panel runs whether or not a lens voted (loss-makers and turnarounds
        # are exactly where a second reading is wanted). With no vote the record is empty, the
        # logs say "no lens vote", and there is nothing for the panel to agree or disagree with.
        record = record_snapshot(report.agreement)
        result.record = record
        config = config or load_config()
        pack = build_blind_pack(report, fundamentals, statements, news, strategies_dir=strategies_dir)
        # the tickers the lenses ranked this company against: the benchmark the scorer will use
        result.peers = [t for t in (getattr(report, "universe", None) or [])
                        if str(t).upper() != str(report.ticker).upper()]
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
