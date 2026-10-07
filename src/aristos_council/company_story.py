"""COMPANY-STORY-1 - the first screen of a company page, written by CODE from templates.

The page used to open on tables and end on diagnostics, and the one readable part (the plain-English
summary) is optional, costs a model call and was missing from three of five hand-test reports. The
first screen now answers three questions in order - what did Aristos conclude, why, and how much
should I trust it - and everything that supports the answer stays on the page below a fold.

Nothing here computes anything. Every sentence is a template filled from figures the run already
holds (votes, exclusion reasons, the accounts' period, the analysts' as-of date), so it is always
present, costs nothing and reads the same every time. No vote, rank, badge or number changes; the
saved results record is untouched. The HTML, Markdown, text and Streamlit renderers all read ONE
``StoryPage`` built here, so the four cannot drift (the design's rule 5).

Reader-text rules (the report sweep checks them on all three exports): a lens is named by its
display label, never its id; no column name; never the word "cohort" or "strategy".
"""
from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .plurals import plural
from .tools.valuation_band import ordinal

SECTION_ANSWER = "The answer"
SECTION_STORY = "The story"
SECTION_TABLE = "Lens by lens"
SECTION_COUNCIL = "Council opinion"
SECTION_WORKINGS = "Show the workings"

NOT_A_PREDICTION = ("This is a ranking against peers by fixed rules. It does not know the company's "
                    "plans, its management, its legal risks or today's news, it is not a price "
                    "target, and it is not advice.")
SUMMARY_WITHHELD_PREFIX = "The model's plain-English summary was withheld"

_NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
_LEGAL_TAIL = (" Corporation", " Corp.", " Corp", " Incorporated", " Inc.", " Inc", " Ltd.", " Ltd",
               " Limited", " PLC", " plc", " Holdings", " Holding", " Company", " Co.",
               " Group", " AG", " SE", " NV", " N.V.", " S.A.", " SA", " Ordinary Shares",
               " Common Stock")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _count(n: int) -> str:
    """"one", "five", then digits past ten."""
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _lenses(n: int) -> str:
    return f"{_count(n)} {'lens' if n == 1 else 'lenses'}"


def _join(names) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


_SHORT_NAMES_FILE = Path(__file__).resolve().parents[2] / "data" / "short_names.yaml"
_SHORT_NAMES: Optional[dict] = None


def load_short_names(path=_SHORT_NAMES_FILE) -> dict:
    """``{TICKER: short name}`` from the small dated file ({} when it is absent or unreadable: a
    missing file only means every company falls back to its cleaned legal name)."""
    try:
        import yaml
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001 - display data, never fatal
        return {}
    return {str(e["ticker"]).upper(): str(e["short"]).strip()
            for e in (doc.get("names") or []) if e.get("ticker") and e.get("short")}


def _listed_short_name(ticker: str) -> str:
    global _SHORT_NAMES
    if _SHORT_NAMES is None:
        _SHORT_NAMES = load_short_names()
    return _SHORT_NAMES.get(str(ticker or "").upper(), "")


def short_name(report) -> str:
    """"Ford", "BYD", "JPMorgan": the company's common short name from ``data/short_names.yaml`` when
    it is listed there (SHORT-NAME-2), else the legal name cut at the first comma, then legal suffixes
    from the end ("JPMorgan Chase & Co." is NOT cut at the ampersand)."""
    listed = _listed_short_name(getattr(report, "ticker", ""))
    if listed:
        return listed
    name = str(getattr(report.check, "company_name", "") or "").strip() or report.ticker
    text = name.split(",")[0].strip()
    changed = True
    while changed:
        changed = False
        for tail in _LEGAL_TAIL:
            if text.endswith(tail) and len(text) > len(tail):
                text = text[: -len(tail)].strip()
                changed = True
    text = text.rstrip(" &").strip()
    return text or report.ticker


_NUM = re.compile(r"[-+]?\d[\d,\.]*")


def _reason_key(reason: str) -> str:
    """The same cause with different figures groups together ("return on invested capital 9.3%; the
    rule requires at least 12%" for two lenses is one reason)."""
    return _NUM.sub("#", reason).strip().lower()


def _plain_reason(reason: str) -> str:
    text = re.sub(r"\s+", " ", (reason or "").strip()).rstrip(". ")
    m = re.match(r"^below min market cap \((.+)\)$", text)
    if m:                                   # "below min market cap ($5.0bn)" reads as a rule name
        return f"market cap below the {m.group(1)} minimum"
    return text


def _first_reason(reason: str) -> str:
    """The first sentence of a reason (a lens that fails several rules states them one after the
    other; the table and the story give the first, the notes under the workings give them all)."""
    text = _plain_reason(reason)
    head = re.split(r"\.\s+(?=[A-Za-z0-9])", text, maxsplit=1)[0]
    return head.rstrip(". ")


def _voting(report) -> list:
    return [v for v in report.votes if v.votes]


def _checks(report) -> list:
    return [v for v in report.votes if not v.votes]


def _excluded_groups(report) -> list[tuple[str, list]]:
    """[(reason, [vote, ...])] for the voting lenses that did not rank the company, biggest group
    first, ties in the order the lenses were ticked. The reason is the first member's own text, so
    its figure is the run's own."""
    groups: dict[str, list] = {}
    order: list[str] = []
    for v in _voting(report):
        if v.ranked:
            continue
        reason = _first_reason(v.reason or v.result())
        key = _reason_key(reason)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((reason, v))
    ranked = sorted(order, key=lambda k: (-len(groups[k]), order.index(k)))
    return [(groups[k][0][0], [v for _r, v in groups[k]]) for k in ranked]


def is_bank(report) -> bool:
    """A confirmed bank or insurer: the debt-and-cash block says it is not meaningful for one."""
    dc = getattr(report.check, "debt_and_cash", None)
    return bool(dc is not None and getattr(dc.net_debt, "not_meaningful", ""))


def accounts_asof(report) -> str:
    """The period of the accounts every absolute reading rests on: "fiscal year to Dec 2025", or
    "latest annual accounts" when the provider carries no dated series."""
    acc = getattr(report.check, "accounts", None) or {}
    m = re.search(r"(fiscal year to \w+ \d{4})", str(acc.get("basis", "")))
    return m.group(1) if m else "latest annual accounts"


def _fetched(report) -> str:
    return str((getattr(report.check, "providers", None) or {}).get("as_of", "") or "")


def _analyst_asof(report) -> str:
    trend = getattr(report.check, "analyst_trend", None)
    return str(getattr(trend, "as_of", "") or _fetched(report))


def under_5bn(report) -> bool:
    """True when the company is under the lenses' $5bn gate: ranked on the opt-in small-company band,
    or simply too small for every lens (its own market cap, from the peer group's subject)."""
    if report.outside_tested_range:
        return True
    from .smallcap_band import SMALLCAP_CEILING_USD
    subject = getattr(getattr(report, "peer_group", None), "subject", None)
    cap = getattr(subject, "market_cap_usd", None)
    return isinstance(cap, (int, float)) and 0 < cap < SMALLCAP_CEILING_USD


def untested_sentence(report) -> str:
    """The sentence for a company outside what the lenses were tested on ("" when it applies not)."""
    if under_5bn(report):
        return "It is also outside the tested range (under $5bn): no track record applies."
    from .company_report import NO_COHORT_TRACK_RECORD_LINE
    if report.track_record_caption == NO_COHORT_TRACK_RECORD_LINE:
        return "No track record exists for this industry yet."
    return ""


def _friendly_caption(caption: str) -> str:
    """The track-record caption without the word "cohort" (reader text)."""
    text = re.sub(r"from the (.+?) cohort,", r"from tests on \1 companies,", caption or "")
    return text.replace(" cohort", " group")


# --------------------------------------------------------------------------- #
# Section 1 - the answer (two lines)
# --------------------------------------------------------------------------- #
def _vote_line(report) -> str:
    name = short_name(report)
    ag = report.agreement
    if ag is None:
        why = _plain_reason(report.no_vote_reason)
        return f"No lens could rank {name}" + (f": {why}." if why else ".")
    if not ag.n_voted:
        return f"No lens voted on {name}."
    voters = [v for v in _voting(report) if v.ranked]
    bank = is_bank(report)
    if ag.n_voted == 1:
        v = voters[0]
        where = f", {ordinal(v.position)} of {v.cohort_size}" + (f", {v.tie_note.strip(' ()')}" if v.tied_with else "") if v.position else ""
        built = "one lens built for banks" if bank else "the one lens that voted"
        return f"{v.word}, on {built} ({v.label}{where})."
    words = {"buy": "BUY", "hold": "HOLD", "sell": "SELL"}
    by = {"buy": ag.buy, "hold": ag.hold, "sell": ag.sell}
    buckets = [k for k in ("buy", "hold", "sell") if by[k]]
    total = _count(ag.n_voted)
    parts = []
    for i, key in enumerate(buckets):
        labels = by[key]
        who = f" ({_join(labels)})" if len(labels) <= 3 else ""
        verb = "says" if len(labels) == 1 else "say"
        if i == 0:
            parts.append(f"{_cap(_count(len(labels)))} of {total} votes {verb} {words[key]}{who}")
        else:
            lead = "the other " if len(buckets) == 2 else ""
            parts.append(f"{lead}{_count(len(labels))} {verb} {words[key]}{who}")
    return "; ".join(parts) + "."


def _reason_line(report) -> str:
    ag = report.agreement
    out: list[str] = []
    groups = _excluded_groups(report)
    n_excl = sum(len(vs) for _r, vs in groups)
    if groups and ag is not None:
        top_reason, top_votes = groups[0]
        if is_bank(report) and ag.n_voted:
            out.append(f"The other {_lenses(n_excl)} {'is' if n_excl == 1 else 'are'} not for banks.")
        elif n_excl == 1:                   # B22-U10: one lens has no "all for one reason" to state
            out.append(f"{'One lens' if ag.n_voted else 'The one lens'} did not apply: {top_reason}.")
        elif len(top_votes) == n_excl:
            if ag.n_voted:
                out.append(f"{_cap(_lenses(n_excl))} did not apply, all for one reason: {top_reason}.")
            else:
                out.append(f"None of the {_lenses(n_excl)} could apply: {top_reason}.")
        elif len(top_votes) == 1:
            out.append(f"{_cap(_lenses(n_excl))} did not apply, each for its own reason "
                       "(listed below).")
        else:
            out.append(f"{_cap(_lenses(n_excl))} did not apply; the most common reason "
                       f"({_count(len(top_votes))} of them): {top_reason}.")
    for v in _checks(report):
        if v.ranked and ag is not None and ag.checks.get(v.label):
            out.append(f"{v.label} reads {ag.checks[v.label]}.")
    tail = untested_sentence(report)
    if tail:
        out.append(tail)
    elif report.track_record_summary and ag is not None and ag.n_voted:
        out.append(report.track_record_summary.rstrip(".") + ".")
    if not out:
        out.append(f"Every ticked lens applied to {short_name(report)}.")
    return " ".join(out)


def answer_lines(report) -> tuple[str, str]:
    """The two-line answer. Always present, always from the run's own figures."""
    return _vote_line(report), _reason_line(report)


# --------------------------------------------------------------------------- #
# Section 2 - the story (five paragraphs)
# --------------------------------------------------------------------------- #
LEADS = ("What this run asked.", "What happened.", "Other facts.", "What to doubt.",
         "What this cannot tell you.")


def _asked(report) -> str:
    name = short_name(report)
    group = report.peer_group
    labels = [v.label for v in report.votes]
    if group is None or not getattr(group, "members", None):
        why = _plain_reason(report.no_vote_reason) or "no peer group could be formed"
        return f"The run asked how {name} ranks against its peers, but {why}, so no lens ran."
    n = len(group.members)
    ran = f"{_lenses(len(labels))} ({_join(labels)})" if labels else "no lens"
    # B22-B5: no "step 2 of 4 of the peer search, market index of ..." here - that detail lives under
    # "How this peer group was built" in the workings.
    return (f"{name} was ranked against {plural(n, 'similar-sized company', 'similar-sized companies')} "
            f"in its industry, under {ran}.")


def _band_sentence(report) -> str:
    c = report.check
    p = getattr(c, "band_percentile", None)
    when = _fetched(report)
    asof = f" (prices to {c.providers.get('price_as_of')})" if (c.providers or {}).get("price_as_of") else ""
    if p is not None:
        return (f"On its own five-year history its valuation sits at the {ordinal(round(p))} "
                f"percentile{asof}, a mark and never a veto.")
    text = str(getattr(c, "valuation_band", "") or "")
    text = re.sub(r"^not evaluated\s*[—-]\s*", "", text).strip()
    if text and text != "—":
        return f"Its valuation band could not be read: {text}."
    return f"Its valuation band was not evaluated ({when})." if when else ""


def _happened(report) -> str:
    ag = report.agreement
    bits: list[str] = []
    ranked = [v for v in _voting(report) if v.ranked]
    if ranked:
        bits.append("Voting: " + "; ".join(f"{v.label} {v.result()}" for v in ranked) + ".")
        note = usable_figures_note(report)
        if note:
            bits.append(note[:1].upper() + note[1:])
    else:
        bits.append("No lens voted.")
    groups = _excluded_groups(report)
    for reason, vs in groups:             # STORY-OTHER-REASON: every reason is named, none left over
        bits.append(f"{_cap(_join([v.label for v in vs]))} did not apply: {reason}.")
    for v in _checks(report):
        if v.ranked and ag is not None and ag.checks.get(v.label):
            bits.append(f"{v.label} marks it {ag.checks[v.label]} and does not vote.")
    band = _band_sentence(report)
    if band:
        bits.append(band)
    return " ".join(bits)


def _leg_text(leg) -> str:
    """The longest window that could be read, else why none could."""
    avail = [leg.cagr[w] for w in sorted(leg.cagr) if leg.cagr[w].available]
    if avail:
        r = avail[-1]
        return r.label.split(" (only ")[0] + (r.caution or "")
    for w in sorted(leg.cagr):
        t = leg.cagr[w].text()
        if t:
            return re.sub(r"^not stated\s*[—-]\s*", "", t)
    return ""


def _survived(report) -> str:
    c = report.check
    asof = accounts_asof(report)
    bits: list[str] = []
    dc = getattr(c, "debt_and_cash", None)
    if dc is not None:
        if getattr(dc.net_debt, "not_meaningful", ""):
            bits.append("Debt and cash do not describe a bank or insurer, so no reading is given.")
        elif dc.net_debt.available:
            yl = getattr(dc.years_to_repay, "label", "") if getattr(dc.years_to_repay, "available", False) else ""
            extra = f", and it {yl.rstrip('.')}" if yl.startswith("would take") else ""
            rw = getattr(dc, "cash_runway", None)           # CASH-RUNWAY-1
            if rw is not None and rw.available:
                extra += (", and " if rw.label.startswith("at ") else "; ") + rw.label.rstrip(".")
            bits.append(f"Debt and cash ({asof}): it {dc.net_debt.label.rstrip('.')}{extra}.")
        else:
            bits.append(f"Debt and cash ({asof}) could not be read: {dc.net_debt.note}.")
    gr = getattr(c, "growth_record", None)
    if gr is not None:
        rev, eps = _leg_text(gr.revenue), _leg_text(gr.eps)
        pieces = [p for p in (rev, eps) if p]
        if pieces:
            bits.append(f"Growth record ({asof}): " + "; ".join(pieces) + ".")
    trend = getattr(c, "analyst_trend", None)
    ratings = getattr(trend, "ratings", None)
    when = _analyst_asof(report)
    if ratings is not None and getattr(ratings, "available", False):
        bits.append(f"Analysts ({when}): {ratings.summary_line()}; "
                    f"{ratings.target_sentence[0].lower() + ratings.target_sentence[1:]}.")
    elif ratings is not None:
        bits.append(f"No analyst ratings are shown ({when}).")
    return " ".join(bits) if bits else "No debt, growth or analyst reading could be made."


def _doubt(report) -> str:
    ag = report.agreement
    bits: list[str] = []
    groups = _excluded_groups(report)
    n_excl = sum(len(vs) for _r, vs in groups)
    if n_excl:
        bits.append(f"{_cap(_lenses(n_excl))} did not apply, so {'it says' if n_excl == 1 else 'they say'} "
                    "nothing either way; that is not a bad rank.")
    for v in _checks(report):
        if ag is not None and ag.checks.get(v.label) == "doubted":
            bits.append(f"{v.label} doubts the quality of the accounts.")
    weak = [v for v in _voting(report) if v.ranked and v.badge is not None
            and v.badge.label != "proven here"]
    if weak:
        bits.append("No badge says proven for " + _join(
            [f"{v.label} ({v.badge.label})" for v in weak]) + ".")
    group = report.peer_group
    if group is not None and getattr(group, "members", None):
        if group.thin:
            bits.append(f"Only {plural(len(group.members), 'peer')} could be found, a thin comparison.")
        elif group.broad:
            bits.append("The peer group had to be widened to the whole sector, so its peers differ more.")
        elif group.step >= 3:
            bits.append(f"The peer search needed step {group.step} of 4, so peers are looser matches.")
    if under_5bn(report):
        bits.append("The company is under $5bn, outside what the lenses were tested on.")
    p = getattr(report.check, "band_percentile", None)
    if p is None and str(getattr(report.check, "valuation_band", "")).startswith("not evaluated"):
        bits.append("Its valuation band abstained.")
    return " ".join(bits) if bits else "Nothing in this run calls for extra doubt beyond the sentence below."


def _cannot(report) -> str:
    # B22-B9: the "no track record" / "outside the tested range" sentence is said ONCE, in the answer
    return NOT_A_PREDICTION


def story_paragraphs(report) -> list[tuple[str, str]]:
    """The five paragraphs, ``[(lead, text)]``, in reading order."""
    return list(zip(LEADS, (_asked(report), _happened(report), _survived(report), _doubt(report),
                            _cannot(report))))


def summary_note(report) -> str:
    """"" unless the model's summary was asked for and withheld: then ONE line says why, and the
    code-written story stays. When the summary was written it REPLACES the story (never two)."""
    s = report.summary
    if s is None or getattr(s, "available", False):
        return ""
    why = _plain_reason(getattr(s, "note", "")) or "it did not pass its checks"
    return f"{SUMMARY_WITHHELD_PREFIX}: {why}."


def usable_figures_note(report) -> str:
    """B22-B3: the group is the company plus its peers (BYD: 20), but each lens ranks only the companies
    that had usable figures for IT (14 for three of them, 20 for Forensic). "" when every lens ranked the
    whole group, so the note appears only where the numbers on the page would otherwise puzzle."""
    group = getattr(report, "peer_group", None)
    members = getattr(group, "members", None)
    if not members:
        return ""
    total = len(members) + 1
    ranked = [(v.label, v.cohort_size) for v in report.votes if v.ranked and v.cohort_size]
    if not ranked or all(n == total for _l, n in ranked):
        return ""
    by_n: dict[int, list[str]] = {}
    for label, n in ranked:
        by_n.setdefault(n, []).append(label)
    parts = []
    for i, (n, labels) in enumerate(by_n.items()):
        parts.append(f"{n} of {total} had usable figures for {_join(labels)}" if i == 0
                     else f"{n} of {total} for {_join(labels)}")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Section 3 - one table
# --------------------------------------------------------------------------- #
TABLE_HEADERS = ["Lens", "Vote or mark", "Badge", "Reason"]


@dataclass(frozen=True)
class TableRow:
    lens: str
    outcome: str
    badge: str
    reason: str
    asks: str = ""
    badge_detail: str = ""
    full_reason: str = ""         # every sentence of the reason and the would-rank text (the notes)

    def cells(self) -> list[str]:
        return [self.lens, self.outcome, self.badge, self.reason]


def _outcome(v) -> str:
    if v.status == "ranked":
        return v.result(with_factor_note=False)          # B22-B8: "ranked on 2 of 3 factors" is Reason's
    if v.status == "excluded":
        return "does not apply"
    if v.status == "too_few":
        return "too few to rank"
    if v.status == "unrateable":
        return "no data"
    if v.status == "fetch_error":
        return "fetch failed"
    if v.status == "no_group":
        return "not run"
    return "not reported"


def _row_reason(v) -> str:
    if v.status == "ranked":
        note = v.factor_note.strip(" ·")
        return note or ("marks only, never votes" if not v.votes else "ranked among its peers")
    if v.status == "excluded":
        text = _first_reason(v.reason)
        w = v.would_rank
        if w is not None and w.available:
            text += f" (would rank {ordinal(w.position)} of {w.cohort_size}; not a vote)"
        return text
    return _plain_reason(v.result())


_SENTENCE_START = re.compile(r"(?<=[.!?] )([a-z])")


def sentence_starts_capital(text: str) -> str:
    """B22-B7a: a sentence that begins after a full stop begins with a capital ("...at least 12%. On its
    measures it would rank..."). Only the first letter after ". " changes; figures like "3.4x" have no
    space after the dot and are untouched."""
    return _SENTENCE_START.sub(lambda m: m.group(1).upper(), text)


def _full_reason(v) -> str:
    if v.status != "excluded":
        return ""
    text = _plain_reason(v.reason)
    if v.would_rank is not None:
        text += f". {v.would_rank.text}"
    full = text if (text != _first_reason(v.reason) or v.would_rank is not None) else ""
    return sentence_starts_capital(full)


def table_rows(report) -> list[TableRow]:
    from .backtest import BADGE_MEANINGS

    rows = []
    for v in report.votes:
        detail = ""
        if v.badge is not None:
            detail = f"{v.badge.detail_line()} - {BADGE_MEANINGS[v.badge.label]}"
        rows.append(TableRow(lens=v.label, outcome=_outcome(v),
                             badge=v.badge.label if v.badge is not None else "none",
                             reason=_row_reason(v), asks=v.asks, badge_detail=detail,
                             full_reason=_full_reason(v)))
    return rows


_FLOOR_NOTE = re.compile(r"tested range starts at \$(\d+(?:\.\d+)?)bn")


def _money_bn(usd: float) -> str:
    return f"${usd / 1e9:.1f}bn" if usd >= 1e9 else f"${usd / 1e6:.0f}m"


def floor_words(report, note: str) -> str:
    """FLOOR-WORDS-1: ONE sentence for the two size floors that used to read as two contradictory
    rules ("below the $5bn rule" / "its industry's tested range starts at $10bn"). "" when the note is
    not the industry-floor note."""
    m = _FLOOR_NOTE.search(note or "")
    if not m:
        return ""
    from .smallcap_band import SMALLCAP_CEILING_USD
    floor = float(m.group(1)) * 1e9
    gate = f"${SMALLCAP_CEILING_USD / 1e9:g}bn"
    subject = getattr(getattr(report, "peer_group", None), "subject", None)
    cap = getattr(subject, "market_cap_usd", None)
    who = short_name(report) + (f" ({_money_bn(cap)})" if isinstance(cap, (int, float)) and cap > 0
                                else "")
    if floor <= SMALLCAP_CEILING_USD:
        return f"Lenses are tested on companies worth {gate} or more. {who} is outside that."
    return (f"Lenses are tested on companies worth {gate} or more; in this industry the backtest "
            f"covered companies from ${floor / 1e9:g}bn up. {who} is outside both.")


def small_company_tag(report) -> list[str]:
    """The small-company caveat, ONCE, above the table (the old per-row tag is gone)."""
    if not report.outside_tested_range:
        return []
    detail = []
    for line in report.tested_range_detail_lines:
        line = floor_words(report, line) or line
        detail.append(re.sub(r"the (.+?) cohort,", r"the \1 companies,", line).replace(" cohort", " group"))
    return [report.tested_range_line, *detail]


def track_caption(report) -> str:
    """The track-record line under the table. A company no backtest covers gets no caption here:
    the Badge column already says "untested here" and the answer says why."""
    from .company_report import NO_COHORT_TRACK_RECORD_LINE
    if report.track_record_caption == NO_COHORT_TRACK_RECORD_LINE:
        return ""
    return _friendly_caption(report.track_record_caption)


# --------------------------------------------------------------------------- #
# The page plan every renderer reads
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StoryPage:
    answer: tuple
    paragraphs: tuple             # ((lead, text), ...) - the code story or the model's summary
    model_summary: bool           # True: ``paragraphs`` is the model's (it replaced the story)
    note: str                     # the one-line "why the model's summary was withheld", or ""
    tag: tuple                    # the small-company lines, once, above the table
    headers: tuple
    rows: tuple
    caption: str                  # the track-record line under the table
    no_vote: str                  # shown instead of the table when no lens was ticked
    group_note: str = ""          # "14 of 20 had usable figures for ..." (B22-B3), muted, under the table


def story_page(report) -> StoryPage:
    summary = report.summary
    if summary is not None and getattr(summary, "available", False):
        from .reader import reader_paragraphs
        paragraphs = tuple(reader_paragraphs(summary.summary, company=True))
        model = True
    else:
        paragraphs = tuple(story_paragraphs(report))
        model = False
    from .company_report import NO_LENS_REASON
    return StoryPage(
        answer=answer_lines(report), paragraphs=paragraphs, model_summary=model,
        note=summary_note(report), tag=tuple(small_company_tag(report)),
        headers=tuple(TABLE_HEADERS), rows=tuple(table_rows(report)), caption=track_caption(report),
        no_vote="" if report.votes else (report.no_vote_reason or NO_LENS_REASON),
        group_note=usable_figures_note(report))


def narration_check_line(report) -> str:
    """One line for the folded workings: how many statements the narration check flagged in the
    council opinion ("" when no council opinion was asked for or none was written)."""
    op = report.council_opinion
    if op is None or not getattr(op, "available", False):
        return ""
    n = (op.narrative or "").count("narration check:")
    if not n:
        return "Narration check: no statement in the council opinion was flagged."
    return (f"Narration check: {plural(n, 'statement')} in the council opinion "
            f"{'was' if n == 1 else 'were'} flagged; each is marked where it appears.")
