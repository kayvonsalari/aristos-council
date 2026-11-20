"""ABSOLUTE-PAGE-1 - the company page built around the absolute verdict (docs/DESIGN_absolute.md section i).

The verdict of record on the company page is now the one the gate and the price test reach (excluded,
not covered, not enough data, qualifies below the hurdle, qualifies and clears it). This module writes, by
CODE and from templates, the parts of the page that say so: the two-line answer (section 1), the five-paragraph
story (section 2) and the context readings that carry no vote (section 6). It computes nothing: every figure is
read off the gate and price-test results the run already holds, so the page, the text, the Markdown and the
HTML (which all read these functions) cannot drift.

Reader-text rules (the report sweep checks them): no lens id, no column name, never "expected return",
"target" or "proven", and never BUY, HOLD or SELL in sections 1 to 6. Those words live only in the relative
view under "Show the workings" and in the judges' own answers.
"""
from __future__ import annotations

import re
from typing import Optional

from .company_story import _band_sentence, _first_reason, short_name
from .tools.price_context import format_money
from .tools.valuation_band import ordinal

LEADS = ("What was asked.", "What the gate found.", "What the price requires.", "What to doubt.",
         "What this cannot tell you.")
CONTEXT_TITLE = "Context, no vote"
CONTEXT_CAPTION = ("Readings that describe the company and carry no vote. None of them changes the answer above.")
RELATIVE_VIEW_TITLE = "Relative view (peer ranks): no longer votes"
RELATIVE_VIEW_CAPTION = ("How the company ranks against similar companies, lens by lens. These ranks used to decide "
                         "the verdict. They no longer decide anything here; they are kept for comparison and "
                         "carry their track records.")
DESIGN_VERSION = "ABSOLUTE-1 phase 3 (docs/DESIGN_absolute.md)"

_STATES = ("excluded", "not_covered", "not_enough_data", "qualifies_below_hurdle", "qualifies_clears_hurdle")
_CASE_SHORT = {"own_5_year": "own 5-year growth", "own_10_year": "own 10-year growth",
               "consensus": "analyst consensus", "downside": "the downside case"}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _pct(v, places: int = 1) -> str:
    return f"{100.0 * v:.{places}f}%"


def _gate(report):
    return getattr(report.check, "gate", None)


def _absolute(report):
    return getattr(report.check, "absolute", None)


def _money(v, ccy) -> str:
    return format_money(v, ccy or None)


def verdict_state(report) -> str:
    """The state of the verdict of record ("" when neither the gate nor the price test ran)."""
    av, gate = _absolute(report), _gate(report)
    if av is not None:
        return av.verdict.state
    return getattr(gate, "state", "") if gate is not None else ""


def verdict_word(report) -> str:
    """"Qualifies, below hurdle" and the like, for the saved record."""
    av = _absolute(report)
    return av.verdict.word if av is not None else ""


def _limit_phrase(limit: str) -> str:
    """"excluded above 0.1" -> "0.1"; "excluded below 5.0%" -> "5.0%"; "excluded at 1 or lower" -> "1 or lower"."""
    m = re.match(r"^excluded (?:above|below|under) (.+)$", limit or "")
    if m:
        return m.group(1)
    m = re.match(r"^excluded at (.+)$", limit or "")
    return m.group(1) if m else (limit or "").strip()


def _reading_phrase(reading: str) -> str:
    return (reading or "").replace("cash flow is ", "cash flow at ")


def _failed_rules(gate) -> list:
    from .gate.engine import FAIL
    return [r for r in (getattr(gate, "rules", ()) or ()) if r.result == FAIL]


def missing_items(report) -> list[str]:
    """What stopped a verdict, as short items: the gate's missing inputs, else the price test's reason."""
    gate, av = _gate(report), _absolute(report)
    if gate is not None and getattr(gate, "missing", ()):
        return [str(m) for m in gate.missing]
    if av is not None and av.verdict.reason:
        reason = re.sub(r"^not enough data:\s*", "", av.verdict.reason.strip(), flags=re.I).rstrip(". ")
        return [reason]
    if gate is not None and gate.reason:
        return [re.sub(r"^not enough data:\s*", "", gate.reason.strip(), flags=re.I).rstrip(". ")]
    return ["the gate and the price test did not run for this company"]


# --------------------------------------------------------------------------- #
# Section 1 - the answer (two lines)
# --------------------------------------------------------------------------- #
def answer_lines(report) -> tuple[str, str]:
    """Two lines, both written by code: the verdict in the design's words, then the one fact that matters most."""
    name = short_name(report)
    gate, av = _gate(report), _absolute(report)
    state = verdict_state(report)
    if state == "excluded":
        failed = _failed_rules(gate)
        if failed:
            r = failed[0]
            first = (f"Excluded: {r.label.lower()} reads {_reading_phrase(r.reading)}, past the limit of "
                     f"{_limit_phrase(r.limit)}.")
            more = [x.label.lower() for x in failed[1:]]
            also = f" It also failed on {', '.join(more[:-1]) + ' and ' + more[-1] if len(more) > 1 else more[0]}." \
                if more else ""
        else:
            first, also = f"Excluded: {_first_reason(getattr(gate, 'reason', '') or 'a check failed')}.", ""
        return first, (f"{name} fails a check on its accounts or its debt, so no return is relied on.{also} "
                       "Any figures below are shown for reference.")
    if state == "not_covered":
        return ("Not covered: banks and insurers are outside this method.",
                f"{name} is a bank or an insurer, so the method works out no return range and gives no verdict.")
    if state == "not_enough_data" or av is None or gate is None:
        items = missing_items(report)
        extra = (" The price test and the forecast-based cases below are shown as information."
                 if av is not None and av.verdict.reason == "no record of the company's own growth" else "")
        return (f"Not enough data: {'; '.join(items)}.",
                f"No verdict is given for {name}: a missing item is neither a pass nor a fail.{extra}")
    h, v = av.hurdle.hurdle, av.verdict
    from .absolute_value.render import range_text
    rng = range_text(av)
    if state == "qualifies_clears_hurdle":
        first = (f"Qualifies, clears hurdle: the price returns {rng} a year if growth holds, above the "
                 f"hurdle of {_pct(h, 2)}.")
    else:
        first = (f"Qualifies, below hurdle: the price returns {rng} a year if growth holds, against a "
                 f"hurdle of {_pct(h, 2)}.")
    if av.implied_growth is not None:
        second = (f"At the hurdle, the price needs {_pct(av.implied_growth)} a year of growth in free cash flow per "
                  f"share for ten years; {v.count_text()}.")
    else:
        second = f"{_cap(v.count_text())}; the growth the price needs could not be worked out ({av.implied_note})."
    return first, second


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


OFFICIAL_LABEL = "Official label"


def plain_answer(report) -> str:
    """PILOT-STRIP-1 item 9: the answer in plain words, said FIRST. One sentence, written by code from the same
    verdict the official label states, for a reader who has never seen the page. "Sound company" is said only
    when no exclusion failed AND no warning fired; with warnings it says how many. The hurdle is called "the
    X% a year this report asks for" (never a "target": that word is kept off the page so a required return is
    not read as a price target or a forecast)."""
    from .gate.engine import WARN
    gate, av = _gate(report), _absolute(report)
    state = verdict_state(report)
    if state == "excluded":
        return "Ruled out: it fails a safety check on its accounts or its debt, so the price is not judged."
    if state == "not_covered":
        return "No answer: banks and insurers are outside what this report can judge."
    if state == "not_enough_data" or av is None or gate is None:
        return "No answer: the data needed to judge this company is missing."
    fired = [w for w in (getattr(gate, "warnings", ()) or ()) if w.result == WARN]
    if fired:
        sound = f"Passes the safety checks, with {len(fired)} warning{'s' if len(fired) != 1 else ''}"
    else:
        sound = "Sound company"
    asked = f"the {_pct(av.hurdle.hurdle, 2)} a year this report asks for"
    v = av.verdict
    if state == "qualifies_clears_hurdle":
        return f"{sound}, and cheap enough at today's price to earn {asked}, if its growth holds."
    if v.of and v.clearing:
        return f"{sound}, but at today's price it earns {asked} in only {v.clearing} of {v.of} growth cases."
    return f"{sound}, but too expensive at today's price to earn {asked}."


def answer_shown(report) -> tuple[str, str]:
    """What every rendering prints under "The answer": the plain sentence, then the official label with its two
    facts. ``answer_lines`` (the official pair) is unchanged and is what the saved record and the summary's facts
    pack keep."""
    first, second = answer_lines(report)
    return plain_answer(report), f"{OFFICIAL_LABEL}: {first} {second}"


# --------------------------------------------------------------------------- #
# Section 2 - the story (five paragraphs)
# --------------------------------------------------------------------------- #
def _asked(report) -> str:
    name = short_name(report)
    av = _absolute(report)
    h = av.hurdle if av is not None else None
    bond = (f"the ten-year government bond yield ({h.currency}) plus 5 points a year"
            if h is not None and h.currency else "the ten-year government bond yield of its currency plus 5 points a year")
    now = f" Today that hurdle is {_pct(h.hurdle, 2)}." if h is not None and h.available else ""
    return (f"The question: would a person who buys {name} at today's price and holds it for ten years more likely "
            f"than not earn more than {bond}?{now} Two steps answer it: a gate that looks for broken accounts and "
            "unsafe debt, then a price test that works out what growth the price needs and what the shares return "
            "if that growth holds.")


# PILOT-STRIP-1 item 12: what each exclusion rule checks, in plain words, said with the rule's name the first time
# the summary mentions it. Keyed by the gate's own rule keys; the versioned gate file is not touched.
RULE_PLAIN = {
    "earnings_quality": "are the reported profits backed by cash",
    "solvency": "does the cash coming in cover the debt",
    "accounting_health": "nine basic health checks on the accounts",
    "cash_runway": "if it is burning cash, how long the cash lasts",
}


def rule_plain(key: str) -> str:
    return RULE_PLAIN.get(key, "")


def _rule_sentence(r, first: bool = False) -> str:
    from .gate.engine import FAIL, NOT_APPLICABLE, NOT_EVALUATED, PASS
    label = r.label if first else r.label.lower()
    if rule_plain(r.key):
        label = f"{label} ({rule_plain(r.key)})"
    if r.result == PASS:
        return f"{label} passes ({r.reading})"
    if r.result == FAIL:
        return f"{label} fails ({r.reading}, limit {_limit_phrase(r.limit)})"
    if r.result == NOT_EVALUATED:
        why = f": {r.note}" if r.note else ""
        return f"{label} could not be evaluated{why}"
    if r.result == NOT_APPLICABLE:
        return f"{label} does not apply"
    return f"{label}: {r.result}"


_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _gate_found(report) -> str:
    gate = _gate(report)
    if gate is None:
        return "The gate could not be run for this company, so nothing here rests on it."
    if gate.state == "not_covered":
        return gate.reason
    if not gate.rules:
        return gate.reason
    from .gate.engine import NOT_EVALUATED, WARN
    parts = "; ".join(_rule_sentence(r, i == 0) for i, r in enumerate(gate.rules))
    n = len(gate.rules)
    parts = f"The gate has {_NUMBER_WORDS.get(n, n)} rules that can rule a company out. {parts}"
    fired = [w for w in gate.warnings if w.result == WARN]
    unread = [w for w in gate.warnings if w.result == NOT_EVALUATED]
    if fired:
        warn = "Warnings fired: " + ", ".join(w.label.lower() for w in fired) + "."
    else:
        warn = "No warning fired."
    if unread:
        warn += " Not evaluated: " + ", ".join(w.label.lower() for w in unread) + "."
    return f"{parts}. {warn}"


def _requires(report) -> str:
    av = _absolute(report)
    if av is None:
        return "The price test could not be run for this company."
    if not av.shown:
        return "No price test is worked out for a bank or an insurer."
    bits = []
    b, p = av.base, av.price
    if b.usable and p.available:
        # PILOT-STRIP-1 item 13, in this order: the spare cash a share produces a year, the price paid, the
        # yield that gives, the growth needed to reach the hurdle, the growth actually achieved.
        bits.append(f"Each share produces {_money(b.base, b.currency)} of spare cash a year (free cash flow: the "
                    f"cash left after running costs and investment; {b.chosen}). The price paid for a share is "
                    f"{_money(p.price, p.currency)}. That is a yield of {_pct(av.yield_on_base)} a year.")
    elif b.not_valued:
        bits.append("The company has no positive free cash flow per share, so the price test has no base.")
    else:
        bits.append(f"The price test has no base: {b.note or p.note or 'free cash flow per share is not available'}.")
    own = {c.key: c for c in av.cases}
    if av.implied_growth is not None:
        recs = [f"{_pct(own[k].growth)} ({w})" for k, w in (("own_5_year", "over five years"),
                                                            ("own_10_year", "over ten years"))
                if k in own and own[k].growth is not None]
        against = f" The growth it actually achieved was {' and '.join(recs)} a year." if recs else ""
        mark = (" That is above the 18% a year few companies have kept up for ten years."
                if av.implied_growth > 0.18 else "")
        bits.append(f"To reach the hurdle, that spare cash must grow {_pct(av.implied_growth)} a year for ten "
                    f"years, flat after that.{mark}{against}")
    elif av.implied_note:
        bits.append(f"The growth the price needs could not be worked out: {av.implied_note}.")
    got = [f"{_CASE_SHORT[c.key]} {c.ret_bound or _pct(c.ret)}" for c in av.cases if c.available]
    if got:
        bits.append("The growth cases return " + ", ".join(got) + " a year.")
    if av.verdict.of and av.verdict.state in ("qualifies_below_hurdle", "qualifies_clears_hurdle"):
        bits.append(f"{_cap(av.verdict.count_text())}.")
    elif av.verdict.reason == "no record of the company's own growth":
        bits.append("Without a record of the company's own growth, no verdict is given; the cases above are information.")
    return " ".join(bits)


def _doubt(report) -> str:
    from .absolute_value.render import EVIDENCE
    from .gate.engine import WARN
    av, gate = _absolute(report), _gate(report)
    bits = []
    if gate is not None:
        fired = [w for w in gate.warnings if w.result == WARN]
        if fired:
            bits.append("Warnings: " + "; ".join(f"{w.label.lower()} ({w.reading})" for w in fired) + ".")
    if av is not None and av.shown:
        gone = [f"{_CASE_SHORT[c.key]} ({c.reason})" for c in av.cases if not c.available and c.reason]
        if gone:
            bits.append("Not available: " + "; ".join(gone) + ".")
        # PILOT-STRIP-1 item 14: the sentence "the consensus case rests on analyst figures that no past data can
        # test" is no longer said here; the tables still label that case "(not testable)".
        hi = [_CASE_SHORT[c.key] for c in av.cases if c.high_growth]
        if hi:
            bits.append(f"Marked above 18% a year: {', '.join(hi)}.")
        if av.yield_high:
            bits.append(f"The yield on the base is {_pct(av.yield_on_base)}, which is unusually high; check the cash flow lines.")
        if av.base.share_basis == "basic shares":
            bits.append("The share count is the basic count, not the diluted one.")
        if av.base.note and av.base.usable:
            bits.append(f"{_cap(av.base.note)}.")
        if av.context.get("investment_warning"):
            bits.append(f"Warning: {av.context['investment_warning']}.")
    if getattr(report, "outside_tested_range", False):
        bits.append("The company is smaller than the range the checks were tested on; it is shown, never blocked.")
    # JUDGE-PANEL-3: only when the panel ran - what the judges could not account for in the facts
    from .judges.render import unexplained_line
    judged = unexplained_line(getattr(report, "judge_panel", None))
    if judged:
        bits.append(judged)
    bits.append(EVIDENCE)
    return " ".join(bits)


def _cannot(report) -> str:
    av = _absolute(report)
    out = ["These are what the price returns if the stated growth repeats. They are not a forecast and not advice. "
           "The method knows nothing of the company's plans, its management or today's news."]
    bt = av.backtest_verdict if av is not None else None
    if bt is not None and av.shown:
        tail = f", {bt.count_text()}" if bt.of else ""
        out.append(f"On the form a backtest could test (no analyst figures) the verdict reads {bt.word}{tail}.")
    from .absolute_backtest.lookup import NO_RESULT, cannot_sentence
    out.append(cannot_sentence(getattr(report, "cohort_slug", None)) or NO_RESULT)
    return " ".join(out)


def story_paragraphs(report) -> list[tuple[str, str]]:
    """The five paragraphs, ``[(lead, text)]``, in reading order."""
    return list(zip(LEADS, (_asked(report), _gate_found(report), _requires(report), _doubt(report),
                            _cannot(report))))


# --------------------------------------------------------------------------- #
# Section 6 - context, no vote
# --------------------------------------------------------------------------- #
def context_row(report) -> str:
    """ABSOLUTE-UI-1: the header's context line. The relative lenses all run and appear only as context, so the
    header counts them: "context: ranked among rivals on 5 measures"."""
    n = sum(1 for v in report.votes if v.ranked)
    if n:
        return f"context: ranked among rivals on {n} {'measure' if n == 1 else 'measures'}"
    return "context: not ranked among rivals"


def rivals_lines(report) -> list[str]:
    """"Where it sits among rivals": one line per relative lens, in the form "where it sits among rivals on <what it
    measures>", with its rank and no vote word."""
    out = []
    for v in report.votes:
        on = f" on {v.measures}" if getattr(v, "measures", "") else ""
        if v.ranked:
            where = (f"{ordinal(v.position)} of {v.cohort_size}" if v.position and v.cohort_size
                     else f"ranked among {v.cohort_size}" if v.cohort_size else "ranked")
            out.append(f"{v.label}: where it sits among rivals{on}: {where}.")
        else:
            why = _first_reason(v.reason or v.result())
            out.append(f"{v.label}: where it sits among rivals{on}: not ranked ({why}).")
    return out


def context_blocks(report) -> list[tuple]:
    """``[("head"|"p"|"note", text)]``: quality readings with the trough year named, momentum, the valuation band,
    earnings over price and earnings power value, the analysts, and where the company sits among rivals."""
    from .absolute_value.render import context_lines
    av = _absolute(report)
    ctx = av.context if av is not None else {}
    out: list[tuple] = [("note", CONTEXT_CAPTION)]
    quality = []
    if ctx.get("worst_roic") is not None:
        quality.append(f"Worst year of return on invested capital in the last five years: "
                       f"{_pct(ctx['worst_roic'])} ({ctx.get('worst_roic_year', '')}).")
    if ctx.get("gross_profitability") is not None:
        quality.append(f"Gross profit over total assets: {_pct(ctx['gross_profitability'])}.")
    if quality:
        out += [("head", "Quality")] + [("p", ln) for ln in quality]
    mom = []
    if ctx.get("momentum_12m") is not None:
        mom.append(f"The share price moved {ctx['momentum_12m']:+.0%} over twelve months"
                   + (f" and {ctx['momentum_6m']:+.0%} over six." if ctx.get("momentum_6m") is not None else "."))
    elif ctx.get("momentum_6m") is not None:
        mom.append(f"The share price moved {ctx['momentum_6m']:+.0%} over six months.")
    if mom:
        out += [("head", "Momentum")] + [("p", ln) for ln in mom]
    val = []
    band = _band_sentence(report)
    if band:
        val.append(band)
    if av is not None:
        val += context_lines(av)
    if val:
        out += [("head", "Valuation")] + [("p", ln) for ln in val]
    trend = getattr(report.check, "analyst_trend", None)
    analysts = []
    ratings = getattr(trend, "ratings", None)
    if ratings is not None and getattr(ratings, "available", False):
        analysts.append(f"{ratings.summary_line()}.")
    if trend is not None:
        analysts += [s for s in (trend.forecast_sentences() or [])]
    if analysts:
        out += [("head", "Analysts")] + [("p", ln) for ln in analysts]
    rivals = rivals_lines(report)
    if rivals:
        out += [("head", "Where it sits among rivals")] + [("p", ln) for ln in rivals]
        measures = dividend_measures_note(report)
        if measures:
            out.append(("note", measures))
    return out


_FCF_SHARE = re.compile(r"dividends took (\d+(?:\.\d+)?%) of free cash flow")
_MEASURED_ON = re.compile(r"Measured on ([^.]+)\.")


def dividend_measures_note(report) -> str:
    """PILOT-STRIP-1 item 18: one page showed the dividend two ways (the gate: "cover 1.3 times, passes"; the
    lenses: "dividends took 120% of free cash flow") and never said they are different measures. When both are on
    the page this says which cash measure each one uses. "" when either is absent."""
    gate = _gate(report)
    cover = next((w for w in (getattr(gate, "warnings", ()) or ())
                  if w.key == "dividend_cover" and w.value is not None), None)
    if cover is None:
        return ""
    for v in report.votes:
        reason = getattr(v, "reason", "") or ""
        m = _FCF_SHARE.search(reason)
        if not m:
            continue
        basis = _MEASURED_ON.search(reason)
        on = f" ({basis.group(1)})" if basis else ""
        how = (cover.note or "average net income / dividends paid").replace(" / ", " divided by the ")
        return (f"The dividend is measured two ways on this page, and they use different money. The gate's dividend "
                f"cover ({cover.value:.1f} times) is on profit: {how}. The lenses' figure (dividends took "
                f"{m.group(1)} of free cash flow) is on cash: the dividends paid against free cash flow{on}. A "
                "dividend can be covered by profit and still be larger than the cash left after investment.")
    return ""


def context_text_lines(report) -> list[str]:
    from .absolute_value.render import block_text_lines
    return [CONTEXT_TITLE.upper(), *block_text_lines(context_blocks(report))]


# --------------------------------------------------------------------------- #
# The saved record
# --------------------------------------------------------------------------- #
def verdict_record(report) -> dict:
    """The verdict of record as saved with a NEW run (additive; every older key keeps its shape)."""
    av, gate = _absolute(report), _gate(report)
    first, second = answer_lines(report)
    return {"state": verdict_state(report), "word": verdict_word(report),
            "answer": [first, second],
            "n_clearing": av.verdict.clearing if av is not None else None,
            "n_cases": av.verdict.of if av is not None else None,
            "backtest_form": (av.backtest_verdict.as_dict() if av is not None and av.backtest_verdict else None),
            "design": DESIGN_VERSION,
            "valuation_definition": getattr(av, "definition_id", "") if av is not None else "",
            "gate_definition": getattr(gate, "definition_id", "") if gate is not None else ""}


def relative_record(report) -> dict:
    """The relative result (the old lens-vote majority) kept apart from the verdict of record."""
    ag = report.agreement
    if ag is None:
        return {"verdict": "", "headline": "", "no_vote_reason": report.no_vote_reason,
                "note": "relative view: no longer decides the verdict"}
    return {"verdict": ag.verdict_of_record, "headline": ag.headline, "record_line": ag.record_line,
            "buy": list(ag.buy), "hold": list(ag.hold), "sell": list(ag.sell),
            "note": "relative view: no longer decides the verdict"}
