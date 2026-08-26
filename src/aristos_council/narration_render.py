"""REPORT-4 — render a structured ``Narration`` into the report's own shapes.

The narrator returns NAMED FIELDS (``agents.schemas.Narration``); this module turns them
into headings, tables and lists. Layout lives here, in code, for one reason: when the
narrator owned it, it invented a different structure every run — "---" rules, ALL-CAPS
pseudo-headings, and on the 2026-08-25 12:54 run a markdown ``##`` that spliced itself
into the document's own heading tree and broke the contents list.

TWO RENDERERS, ONE SOURCE. ``narration_markdown`` and ``narration_html`` read the same
object, so the canonical ``.md`` and the shared ``.html`` cannot drift into saying
different things. The markdown carries the full structure without colour or anchors,
because the ``.md`` is the record and must stand alone.

CONTENT RULES ARE UNCHANGED and still enforced by the ORDER this renders in: every lens's
verdict first, before any prose (NARR-UNION-1); attribution lens by lens; disagreement
reported and left standing; no adjudication anywhere. A renderer cannot add a stance the
narrator did not write, which is precisely why the order is fixed here rather than asked
for in a prompt.
"""

from __future__ import annotations

import html as _html
import re
from typing import Iterable, Optional

# A specialist that could not assess renders with NO stance word and NO confidence
# number. "abstain, confidence 0.00" reads as a measured neutral; the truth is that the
# channel was dark. House rule 3 in presentation form: null is NOT EVALUATED, not zero.
NOT_ASSESSED = "not assessed"

# SCORE-NAME-1 — "#1 of 6 (Growth)" is correct and unexplained: a reader cannot tell why
# one lens's cohort is 6 and another's 18. Stated ONCE per narration section, where the
# first position appears.
COHORT_SIZE_NOTE = ("Each lens ranks only the names that passed its own screen, so "
                    "cohort sizes differ per lens.")

_SECTION_TITLES = {
    "verdict": "Ranker verdict",
    "lens_verdicts": "Every lens's verdict",
    "attribution": "Why each lens ranked it there",
    "disagreement": "Where the lenses disagree",
    "series": "Reported series",
    "context": "Neutral context",
    "specialists": "Specialist views",
    "questions": "Open questions",
}

# ----------------------------------------------------------------------------- #
# Format policing — REPORTED, never silently stripped
# ----------------------------------------------------------------------------- #
# The spec is explicit: layout markup arriving from the narrator is a PROMPT failure, so
# it must be visible rather than quietly cleaned up at render time. These detectors give
# the tests something exact to assert and give a live run something to surface.
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", re.M)
_MD_HEADING = re.compile(r"^\s*#{1,6}\s+\S", re.M)
_ALLCAPS = re.compile(r"^\s*[A-Z][A-Z0-9 ,.'/&()\-‐-―:]{7,}\s*$", re.M)
_BULLET = re.compile(r"^\s*[-*•]\s+\S", re.M)
_QUOTED_NUMBER = re.compile(r'["“”]\s*-?[\d][\d,]*\.?\d*\s*%?\s*["“”]')


def _prose_fields(narration) -> list[tuple[str, str]]:
    """(field name, text) for every free-text field the narrator writes."""
    out: list[tuple[str, str]] = [
        ("echoed_verdict", narration.echoed_verdict or ""),
        ("disagreement_note", narration.disagreement_note or ""),
    ]
    out += [("neutral_context", c) for c in narration.neutral_context]
    out += [("open_questions", q) for q in narration.open_questions]
    for a in narration.lens_attribution:
        out.append(("lens_attribution", a.reasoning or ""))
    for s in narration.specialist_views:
        out.append(("specialist_views", s.reasoning or ""))
        out.append(("specialist_views", s.not_assessed_reason or ""))
    return [(k, v) for k, v in out if v.strip()]


def as_narration(value):
    """Coerce whatever the state carries into a ``Narration``.

    ``state.Decision.narration`` is typed loosely so ``state.py`` need not import the
    agent schemas, which means a round-trip through ``model_validate`` can hand this
    layer a plain dict. Returns ``None`` for anything that is not a narration, so every
    caller's "no structured narration -> render the prose" fallback still fires."""
    if value is None:
        return None
    if hasattr(value, "lens_verdicts"):
        return value
    if isinstance(value, dict):
        from .agents.schemas import Narration

        try:
            return Narration.model_validate(value)
        except Exception:
            return None
    return None


def narration_prose(narration) -> str:
    """Every text field the narrator wrote, flattened into checkable prose.

    This is what ``DecisionOutput.rationale`` becomes, so the fact-checking layer
    (``check_narration`` / ``check_narration_by_lens`` / ``check_cross_lens``) reads the
    narrator's claims exactly as it did when the rationale WAS the narration — one text,
    sentence by sentence. Structuring the output must not quietly retire the checks that
    caught three separate classes of false claim; it must feed them the same words.

    Lens attribution keeps its lens NAME in the sentence, because the per-lens router
    decides which rank table a claim is judged against by the lens the sentence names."""
    parts: list[str] = []
    if narration.echoed_verdict:
        parts.append(narration.echoed_verdict.strip())
    for a in narration.lens_attribution:
        ranks = ", ".join(f"{fr.factor} rank {fr.rank} of {fr.cohort_size}"
                          for fr in a.factor_ranks)
        lead = f"{a.lens} lens"
        if ranks:
            parts.append(f"{lead}: {ranks}.")
        if a.screens_passed:
            parts.append(f"{lead} screens passed: {', '.join(a.screens_passed)}.")
        if a.reasoning:
            parts.append(f"{lead}. {a.reasoning.strip()}")
    if narration.disagreement_note:
        parts.append(narration.disagreement_note.strip())
    # MONEY-ABBREV-1: the checker reads FULL precision. Abbreviation is a display
    # concern; a rendered "$69.7bn" must never make an honest figure look unverifiable,
    # so the prose the checker sees carries the exact number the ledger holds.
    for s in narration.money_series:
        _, full = format_series(s)
        if full:
            parts.append(full)
    parts += [c.strip() for c in narration.neutral_context if c.strip()]
    for s in narration.specialist_views:
        if s.assessed and s.reasoning:
            parts.append(f"{s.specialist}: {s.reasoning.strip()}")
        elif not s.assessed and s.not_assessed_reason:
            parts.append(f"{s.specialist} not assessed: {s.not_assessed_reason.strip()}")
    parts += [q.strip() for q in narration.open_questions if q.strip()]
    return "\n\n".join(p for p in parts if p)


def split_stamps(text: str) -> tuple[str, list[str]]:
    """``(prose, stamps)`` — the ⚠ narration-check lines the pipeline APPENDED, lifted
    back out so they can be rendered as their own callouts beside the structured
    sections rather than trailing the flattened prose."""
    prose, stamps = [], []
    for line in (text or "").splitlines():
        (stamps if line.lstrip().startswith("[⚠") else prose).append(line)
    return "\n".join(prose).strip(), stamps


def format_problems(narration) -> list[str]:
    """Layout markup or quoted numbers the narrator should not have emitted.

    Empty for a well-formed narration. Non-empty means the PROMPT failed, which is why
    this reports rather than repairs — a stripped marker leaves no evidence that the
    contract was broken, and the next run breaks it again."""
    problems: list[str] = []
    for field, text in _prose_fields(narration):
        for pattern, what in ((_RULE, "a horizontal rule"),
                              (_MD_HEADING, "a markdown heading"),
                              (_ALLCAPS, "an ALL-CAPS heading"),
                              (_BULLET, "a bullet marker"),
                              (_QUOTED_NUMBER, "a number in quotation marks")):
            if pattern.search(text):
                problems.append(f"{field}: {what}")
    # de-duplicate, keep first-seen order
    seen, unique = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# ----------------------------------------------------------------------------- #
# Shared shaping — the two renderers agree because they call these
# ----------------------------------------------------------------------------- #
def format_series(series) -> tuple[str, str]:
    """``(rendered, full_precision)`` for one multi-period money series.

    ``"Annual free cash flow: FY2022 $64.1bn · FY2023 $70.0bn · FY2024 $29.0bn ·
    FY2025 $69.7bn"`` — OLDEST FIRST, always, and labelled. When the statements carry no
    fiscal labels the order is STATED ("4 periods, oldest first") rather than implied:
    "preceded by" left a reader unable to tell a halving from a recovery.

    The second element is the same series unabbreviated, for a hover title — the exact
    figures stay one gesture away and the ledger is untouched."""
    from .tools.price_context import format_money, format_money_full

    periods = [p for p in series.periods if p.value is not None]
    if not periods:
        return "", ""
    labelled = all((p.period or "").strip() for p in periods)
    lead = (series.label or "Series").strip()

    def _join(fmt) -> str:
        parts = []
        for p in periods:
            money = fmt(p.value, series.currency or None)
            parts.append(f"{p.period.strip()} {money}" if labelled else money)
        return " · ".join(parts)

    order = "" if labelled else f" ({len(periods)} periods, oldest first)"
    rendered = f"{lead}{order}: " + _join(
        lambda v, c: format_money(v, c, abbreviate=True))
    full = f"{lead}: " + _join(format_money_full)
    return rendered, full


def money_text(text: str) -> str:
    """One narration text field, with every money amount at or above a million rendered
    through the SHARED helper (MONEY-ABBREV-2).

    The narrator writes these fields as prose, so a structured-field-only rule never
    reached them — the 2026-08-26 15:40 report still carried "USD 28,989,000,000" in
    three separate places. Display only: the ledger and the fact-checker keep full
    precision (``narration_prose`` is built from the UNABBREVIATED text)."""
    from .tools.price_context import abbreviate_money_in_text

    # MOMENTUM-GLOSS-1 rides the same seam: one pass over model-written prose, applying
    # display rules the narrator cannot be trusted to apply itself.
    return gloss_momentum_in_text(abbreviate_money_in_text(text or "")[0])


def money_title(text: str) -> str:
    """The hover title for a field whose amounts were abbreviated — the exact figures,
    or "" when nothing was abbreviated (no title, rather than an empty one)."""
    from .tools.price_context import abbreviate_money_in_text

    swaps = abbreviate_money_in_text(text or "")[1]
    return " · ".join(f"{short} = {full}" for short, full in swaps)


def _position(item) -> str:
    """``#2 of 12`` — or the exclusion reason, which has no ordinal to state."""
    if item.position is None or item.cohort_size is None:
        return ""
    return f"#{item.position} of {item.cohort_size}"


def _rank_cell(fr) -> str:
    return f"{fr.rank} of {fr.cohort_size}"


# SPEC-ROLE-1 — what each specialist is FOR, in one line.
#
# The panel lists four stances and never says what question each one answers, so a reader
# cannot tell why the technical specialist's neutral and the fundamental's bullish are not
# a contradiction. These are FIXED strings held here beside the renderer, deliberately NOT
# narrator-generated and NOT in the prompt: a role that varied per run would be one more
# thing to fact-check, and the roles do not vary.
SPECIALIST_ROLES = {
    "fundamental": ("Is this a good business, and can it keep paying what it pays? "
                    "Reads the company's own accounts: profitability, cash flow, "
                    "payout."),
    "technical": ("What is the price chart doing? Reads price against its own averages, "
                  "distance from the 52-week high, volatility. A pullback alone is not "
                  "bearish."),
    "sentiment": ("What do the news and analysts say? Reads headlines and analyst "
                  "recommendation counts only. Abstains when no news data exists."),
    "risk": ("What could go wrong? Reads payout stretch, volatility, data-quality flags, "
             "anything unverifiable."),
}

# The standing line under the panel. Specialists inform the COMMENTARY; the ranker owns
# the verdict, and no stance here can move it.
SPECIALIST_STANDING_NOTE = ("Specialists inform the commentary only — no specialist "
                            "can change a verdict.")


def specialist_role(name: str) -> str:
    """The fixed role line for a specialist, or "" for one we have no line for —
    omitted rather than invented."""
    return SPECIALIST_ROLES.get((name or "").strip().lower(), "")


def _specialist_cells(view) -> tuple[str, str, str]:
    """``(stance, confidence, why)`` — a NOT ASSESSED specialist carries neither a stance
    word nor a confidence number, only its cause."""
    if not view.assessed:
        return NOT_ASSESSED, "—", money_text((view.not_assessed_reason or "").strip())
    stance = (view.stance or "").strip() or "—"
    conf = "—" if view.confidence is None else f"{view.confidence:.2f}"
    return stance, conf, money_text((view.reasoning or "").strip())


def narration_sections(narration) -> list[tuple[str, str]]:
    """``(anchor-slug, title)`` for the sections this narration will actually render —
    so a contents list can be built without rendering the document twice."""
    out = [("verdict", _SECTION_TITLES["verdict"])] if narration.echoed_verdict else []
    if narration.lens_verdicts:
        out.append(("lens_verdicts", _SECTION_TITLES["lens_verdicts"]))
    if narration.lens_attribution:
        out.append(("attribution", _SECTION_TITLES["attribution"]))
    if narration.disagreement_note:
        out.append(("disagreement", _SECTION_TITLES["disagreement"]))
    if narration.money_series:
        out.append(("series", _SECTION_TITLES["series"]))
    if narration.neutral_context:
        out.append(("context", _SECTION_TITLES["context"]))
    if narration.specialist_views:
        out.append(("specialists", _SECTION_TITLES["specialists"]))
    if narration.open_questions:
        out.append(("questions", _SECTION_TITLES["questions"]))
    return out


# ----------------------------------------------------------------------------- #
# Markdown — the canonical record, so it carries the whole structure unaided
# ----------------------------------------------------------------------------- #
def narration_markdown(narration, *, level: int = 4,
                       stamps: Optional[Iterable[str]] = None,
                       issues: Optional[Iterable] = None) -> str:
    """One name's narration as markdown. ``level`` is the heading depth so the sections
    nest UNDER the name's own heading instead of competing with it.

    NARR-SCHEMA-1: ``issues`` renders a structural warning BANNER first — before the
    narration it describes, because a reader must know the contract broke before reading
    what broke it. The narration itself is untouched (annotate, never rewrite)."""
    from .narration_schema import BANNER_NOTE, BANNER_TITLE, banner_lines

    h = "#" * level
    out: list[str] = []

    warnings = banner_lines(issues or [])
    if warnings:
        out += [f"> **⚠ {BANNER_TITLE}**", ">", f"> {BANNER_NOTE}", ">"]
        out += [f"> - {w}" for w in warnings]
        out.append("")

    if narration.echoed_verdict:
        out += [f"{h} {_SECTION_TITLES['verdict']}", "",
                f"**{money_text(narration.echoed_verdict.strip())}**", ""]

    if narration.lens_verdicts:
        out += [f"{h} {_SECTION_TITLES['lens_verdicts']}", "",
                "Stated in full before any prose — including the lenses that did not "
                "buy.", "",
                "| Lens | Verdict | Position | Note |", "| --- | --- | --- | --- |"]
        for v in narration.lens_verdicts:
            note = (v.excluded_reason or "").strip() or "—"
            out.append(f"| {v.lens} | {v.verdict.upper()} | "
                       f"{_position(v) or '—'} | {note} |")
        out.append("")

    if narration.lens_attribution:
        out += [f"{h} {_SECTION_TITLES['attribution']}", "",
                f"_{COHORT_SIZE_NOTE}_", ""]
    for a in narration.lens_attribution:
        out += [f"{'#' * (level + 1)} {a.lens} — why", ""]
        if a.factor_ranks:
            out += ["| Factor | Rank | Note |", "| --- | --- | --- |"]
            out += [f"| {fr.factor} | {_rank_cell(fr)} | {(fr.note or '—')} |"
                    for fr in a.factor_ranks]
            out.append("")
        if a.factor_score is not None:
            out += [f"Factor score: {a.factor_score:g}", ""]
        if a.screens_passed:
            out += ["Screens passed: " + ", ".join(a.screens_passed), ""]
        if a.reasoning:
            out += [money_text(a.reasoning.strip()), ""]

    if narration.disagreement_note:
        out += [f"{h} {_SECTION_TITLES['disagreement']}", "",
                money_text(narration.disagreement_note.strip()), "",
                "_Reported and left standing — the ranker's verdicts are unchanged._", ""]

    series_lines = [format_series(s) for s in narration.money_series]
    series_lines = [(r, f) for r, f in series_lines if r]
    if series_lines:
        out += [f"{h} {_SECTION_TITLES['series']}", ""]
        out += [f"- {rendered}" for rendered, _ in series_lines]
        out.append("")

    if narration.neutral_context:
        out += [f"{h} {_SECTION_TITLES['context']}", ""]
        out += [f"- {money_text(c.strip())}" for c in narration.neutral_context]
        out.append("")

    if narration.specialist_views:
        out += [f"{h} {_SECTION_TITLES['specialists']}", ""]
        for view in narration.specialist_views:
            stance, conf, why = _specialist_cells(view)
            role = specialist_role(view.specialist)
            out.append(f"**{view.specialist}** — {stance}"
                       + (f", confidence {conf}" if conf != "—" else ""))
            if role:
                out.append(f"_{role}_")
            out += ["", why or "—", ""]
        out += [f"_{SPECIALIST_STANDING_NOTE}_", ""]

    if narration.open_questions:
        out += [f"{h} {_SECTION_TITLES['questions']}", ""]
        out += [f"- {money_text(q.strip())}" for q in narration.open_questions]
        out.append("")

    for stamp in (stamps or []):
        out += [stamp, ""]

    return "\n".join(out).rstrip() + "\n"


# ----------------------------------------------------------------------------- #
# HTML — the same sections as real elements, styled like the rest of the report
# ----------------------------------------------------------------------------- #
def _esc(text: str) -> str:
    return _html.escape(str(text), quote=False)


def _p_money(text: str) -> str:
    """A paragraph whose money is abbreviated, carrying the exact figures on hover."""
    title = money_title(text)
    attr = f' title="{_html.escape(title, quote=True)}"' if title else ""
    return f"<p{attr}>{_esc(money_text(text))}</p>"


def _li_money(text: str) -> str:
    title = money_title(text)
    attr = f' title="{_html.escape(title, quote=True)}"' if title else ""
    return f"<li{attr}>{_esc(money_text(text))}</li>"


def _table(headers: list[str], rows: list[list[str]], *, cls: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>"
                   for r in rows)
    return (f'<div class="tablewrap"><table{klass}><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def narration_html(narration, *, anchor_prefix: str = "",
                   stamps: Optional[Iterable[str]] = None,
                   issues: Optional[Iterable] = None,
                   callout=None) -> str:
    """One name's narration as HTML — the same sections, in the same order, as the
    markdown. ``anchor_prefix`` ids each sub-section so the contents list can reach it."""
    from .narration_schema import BANNER_NOTE, BANNER_TITLE, banner_lines

    def _id(slug: str) -> str:
        return f' id="{anchor_prefix}-{slug}"' if anchor_prefix else ""

    out: list[str] = []

    warnings = banner_lines(issues or [])
    if warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in warnings)
        out.append(f'<div class="callout structural"{_id("structure")}>'
                   f"<strong>⚠ {_esc(BANNER_TITLE)}</strong>"
                   f"<p>{_esc(BANNER_NOTE)}</p><ul>{items}</ul></div>")

    if narration.echoed_verdict:
        out.append(f'<section class="narr-block"{_id("verdict")}>'
                   f'<h4>{_esc(_SECTION_TITLES["verdict"])}</h4>'
                   f'<p class="echoed">{_esc(money_text(narration.echoed_verdict.strip()))}</p>'
                   "</section>")

    if narration.lens_verdicts:
        rows = [[v.lens, v.verdict.upper(), _position(v) or "—",
                 (v.excluded_reason or "").strip() or "—"]
                for v in narration.lens_verdicts]
        out.append(f'<section class="narr-block"{_id("lens_verdicts")}>'
                   f'<h4>{_esc(_SECTION_TITLES["lens_verdicts"])}</h4>'
                   '<p class="note">Stated in full before any prose — including the '
                   "lenses that did not buy.</p>"
                   + _table(["Lens", "Verdict", "Position", "Note"], rows,
                            cls="lens-verdicts") + "</section>")

    if narration.lens_attribution:
        blocks = []
        for a in narration.lens_attribution:
            part = [f"<h5>{_esc(a.lens)} — why</h5>"]
            if a.factor_ranks:
                part.append(_table(["Factor", "Rank", "Note"],
                                   [[fr.factor, _rank_cell(fr), fr.note or "—"]
                                    for fr in a.factor_ranks]))
            if a.factor_score is not None:
                part.append(f'<p class="screens">Factor score: '
                            f"{a.factor_score:g}</p>")
            if a.screens_passed:
                part.append('<p class="screens">Screens passed: '
                            + _esc(", ".join(a.screens_passed)) + "</p>")
            if a.reasoning:
                part.append(_p_money(a.reasoning.strip()))
            blocks.append("".join(part))
        out.append(f'<section class="narr-block"{_id("attribution")}>'
                   f'<h4>{_esc(_SECTION_TITLES["attribution"])}</h4>'
                   f'<p class="note">{_esc(COHORT_SIZE_NOTE)}</p>'
                   + "".join(blocks) + "</section>")

    if narration.disagreement_note:
        out.append(f'<section class="narr-block"{_id("disagreement")}>'
                   f'<h4>{_esc(_SECTION_TITLES["disagreement"])}</h4>'
                   + _p_money(narration.disagreement_note.strip())
                   + '<p class="note">Reported and left standing — the ranker\'s '
                   "verdicts are unchanged.</p></section>")

    series_lines = [format_series(s) for s in narration.money_series]
    series_lines = [(r, f) for r, f in series_lines if r]
    if series_lines:
        items = "".join(
            f'<li title="{_html.escape(full, quote=True)}">{_esc(rendered)}</li>'
            for rendered, full in series_lines)
        out.append(f'<section class="narr-block"{_id("series")}>'
                   f'<h4>{_esc(_SECTION_TITLES["series"])}</h4>'
                   f"<ul>{items}</ul></section>")

    if narration.neutral_context:
        items = "".join(_li_money(c.strip()) for c in narration.neutral_context)
        out.append(f'<section class="narr-block"{_id("context")}>'
                   f'<h4>{_esc(_SECTION_TITLES["context"])}</h4>'
                   f"<ul>{items}</ul></section>")

    if narration.specialist_views:
        blocks = []
        for view in narration.specialist_views:
            stance, conf, why = _specialist_cells(view)
            role = specialist_role(view.specialist)
            head = _esc(view.specialist) + " — " + _esc(stance)
            if conf != "—":
                head += f", confidence {_esc(conf)}"
            part = [f"<h5>{head}</h5>"]
            if role:
                part.append(f'<p class="note">{_esc(role)}</p>')
            part.append(_p_money(why or "—"))
            blocks.append("".join(part))
        out.append(f'<section class="narr-block"{_id("specialists")}>'
                   f'<h4>{_esc(_SECTION_TITLES["specialists"])}</h4>'
                   + "".join(blocks)
                   + f'<p class="note">{_esc(SPECIALIST_STANDING_NOTE)}</p></section>')

    if narration.open_questions:
        items = "".join(_li_money(q.strip()) for q in narration.open_questions)
        out.append(f'<section class="narr-block"{_id("questions")}>'
                   f'<h4>{_esc(_SECTION_TITLES["questions"])}</h4>'
                   f"<ul>{items}</ul></section>")

    if callout is not None:
        out.extend(callout(s, label="narration check") for s in (stamps or []))

    return "".join(out) or "<p>(no narrative produced)</p>"


# --------------------------------------------------------------------------- #
# MOMENTUM-GLOSS-1 — the 12m/6m pair reads as two bare numbers without it
# --------------------------------------------------------------------------- #
# "The 12-month price return is -10.3% and the 6-month return is +32.4%" states two facts
# and leaves their RELATIONSHIP — which is the whole signal — for the reader to work out.
# The gloss is computed from the two signs, with fixed wording per case, so the narrator
# never freestyles an interpretation: four inputs, four outputs, no judgement.
_MOMENTUM_GLOSS = {
    (True, True): "rising over both windows — a sustained advance",
    (False, False): "falling over both windows — sustained weakness",
    (False, True): "fell early, recovering since",
    (True, False): "rose early, giving it back since",
}

_R12 = re.compile(r"12[-\s]?month[^.]{0,40}?(-?\+?\d+(?:\.\d+)?)\s*%", re.I)
_R6 = re.compile(r"6[-\s]?month[^.]{0,40}?(-?\+?\d+(?:\.\d+)?)\s*%", re.I)


def momentum_gloss(return_12m: float, return_6m: float) -> str:
    """The plain-English clause for one 12m/6m pair. Sign-driven and total."""
    return _MOMENTUM_GLOSS[(return_12m >= 0, return_6m >= 0)]


def gloss_momentum_in_text(text: str) -> str:
    """Append the gloss to any sentence stating BOTH returns.

    Only fires when both figures are present in the same sentence — one return alone has
    no relationship to describe, and inventing one would be exactly the freestyling this
    replaces."""
    if not text:
        return text
    out = []
    for sentence in re.split(r"(?<=\.)\s+", text):
        m12, m6 = _R12.search(sentence), _R6.search(sentence)
        if m12 and m6:
            try:
                r12 = float(m12.group(1).replace("+", ""))
                r6 = float(m6.group(1).replace("+", ""))
            except ValueError:                               # pragma: no cover
                out.append(sentence)
                continue
            gloss = momentum_gloss(r12, r6)
            if gloss not in sentence:
                sentence = sentence.rstrip(". ") + f" — {gloss}."
        out.append(sentence)
    return " ".join(out)
