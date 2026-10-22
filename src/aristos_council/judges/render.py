"""JUDGE-PANEL-1/3 - the panel as cards and lines, ONE source for the page, the text, the Markdown and
the HTML so the four cannot drift (the same rule the company page's story follows).

A judge's reasoning carries a source tag on every claim, ``[pack]`` or ``[own knowledge]``. Where
the format allows, the own-knowledge claims are visibly marked (italic plus a small tag) so a
reader sees at a glance what came from outside the evidence.
"""
from __future__ import annotations

import re

import html as _html

from aristos_council.plurals import plural

from . import answer as _answer
from .panel import (INCOMPLETE, STABLE, UNSTABLE, PanelResult, needs_reading)

SECTION_TITLE = "Judge panel"
CHECKBOX_LABEL = "Judge panel (blind, no vote)"
CAPTION = ("Independent models were each shown this company's facts - the full statements, the "
           "readings, the news - but no lens, rank, badge or verdict, and asked twice. They have no "
           "vote and change nothing above: the lenses stay the verdict of record. This is a record "
           "kept to see, in a year, whether model judgement adds anything.")
READING_CAPTION = "A reading of the difference, not a recommendation."
OWN_TAG_NOTE = "Passages marked “own knowledge” come from the model, not from the facts it was given."
NO_OWN_TAG_NOTE = "No passage was marked own knowledge in this run."


def own_tag_note(result) -> str:
    """The footer: the standing note, or - when no judge marked a single passage as own knowledge -
    the plain statement that none was (OWN-KNOWLEDGE-COUNT-1)."""
    cs = cards(result)
    answered = [c for c in cs if c["claims_pack"] or c["claims_own"]]
    if answered and not any(c["claims_own"] for c in cs):
        return NO_OWN_TAG_NOTE
    return OWN_TAG_NOTE


def claims_line(card) -> str:
    """"5 claims from the pack, 1 from own knowledge", or "" for a judge that gave no answer."""
    if not (card["claims_pack"] or card["claims_own"]):
        return ""
    return (f"{plural(card['claims_pack'], 'claim')} from the pack, "
            f"{card['claims_own']} from own knowledge")

HELP = ("Three independent models (Claude, OpenAI, Gemini), each shown this company's facts - the "
        "full statements, the readings, the news - but NO lens, rank, badge or verdict, and asked "
        "the same question twice. They may use what they know about the company, and must label it. "
        "They never vote and change nothing above; every answer is logged so that in a year we can "
        "see whether model judgement adds anything. A judge with no key is skipped. Off unless you "
        "tick it.")

# the labelled fields a card shows, in order, with their captions
FIELDS = (("driving_fact", "The fact that drove it"), ("reasoning", "Why"), ("key_risk", "Key risk"),
          ("unexplained", "Could not account for"), ("would_change_my_mind", "Would change its mind if"))


def checkbox_caption(config, environ=None) -> str:
    """The expected call count, shown beside the box."""
    runnable = config.runnable(environ)
    calls = config.expected_calls(environ)
    if not runnable:
        return "No judge has a key set, so nothing would run (OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY)."
    missing = [j.label for j in config.judges if j not in runnable]
    tail = f" · {', '.join(missing)}: no key, skipped" if missing else ""
    return (f"About {calls} model calls ({len(runnable)} judge{'s' if len(runnable) != 1 else ''} "
            f"x {config.calls_per_judge}, plus one short paragraph if the panel differs){tail}.")


# --------------------------------------------------------------------------- #
# marking the source of a claim
# --------------------------------------------------------------------------- #
def mark_markdown(text: str) -> str:
    """Own-knowledge claims in italics followed by a small tag; pack claims as written, tag dropped."""
    out = []
    for claim, source in _answer.segments(text):
        if source == "own knowledge":
            out.append(f"*{claim}* `own knowledge`")
        else:
            out.append(claim)
    return " ".join(out) if out else text


def mark_html(text: str) -> str:
    out = []
    for claim, source in _answer.segments(text):
        if source == "own knowledge":
            out.append(f'<em>{_html.escape(claim)}</em> <span class="tag own-knowledge">own knowledge</span>')
        else:
            out.append(_html.escape(claim))
    return " ".join(out) if out else _html.escape(text)


def mark_text(text: str) -> str:
    """Plain text keeps the tags as written - they are the marking."""
    return text


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #
# JUDGE-TAGS-2: the source tag is bookkeeping for the LABELLED fields (the fact that drove it, the
# reasoning) - the ones whose provenance the reader must see. Key risk, could-not-account-for and
# would-change-its-mind are free sentences: a tag a judge put there anyway is dropped, the text shown
# plain. (Live: OpenAI on KO rendered "- *Could not account for:* *nothing.* `own knowledge`".)
PLAIN_FIELDS = _answer.FREE


def _field(key: str, text: str) -> str:
    return _answer.strip_tags(text) if key in PLAIN_FIELDS else (text or "")


def cards(result: PanelResult) -> list[dict]:
    """One dict per judge: the head line fields, then each labelled field (empty for a judge that
    gave no answer - an error shows its short label and NOTHING else).

    Only an UNSTABLE judge (two valid answers that differ) shows its answers side by side, each
    prefixed with its verdict; ``card["sides"][key]`` holds them as ``[(verdict, text), ...]`` so a
    renderer can mark each side on its own. An INCOMPLETE judge (one call answered) shows that one
    answer plain, as a stable one does - its verdict is already in the head line."""
    attrs = {"driving_fact": "driving_fact", "reasoning": "reason", "key_risk": "key_risk",
             "unexplained": "unexplained", "would_change_my_mind": "would_change_my_mind"}
    out = []
    for j in result.judges:
        card = {"judge": f"{j.label} ({j.model})", "verdict": j.verdict_text(), "confidence": "",
                "status": j.status, "own_knowledge": 0, "claims_pack": 0, "claims_own": 0,
                "sides": {}, **{key: "" for key, _ in FIELDS}}
        ok = [c for c in j.calls if c.status == "ok"]
        if j.status in (STABLE, INCOMPLETE) and ok:
            c = ok[0]
            card["confidence"] = c.confidence
            card.update({key: _field(key, getattr(c, attr)) for key, attr in attrs.items()})
        elif j.status == UNSTABLE and ok:
            card["confidence"] = " / ".join(c.confidence for c in ok)
            for key, attr in attrs.items():
                sides = [(c.verdict, _field(key, getattr(c, attr))) for c in ok]
                card["sides"][key] = sides
                card[key] = " | ".join(f"{v}: {t}" for v, t in sides)
        card["own_knowledge"] = sum(c.own_knowledge_claims for c in ok)
        if ok:                                         # the claims on the card as displayed (the first answer)
            card["claims_pack"], card["claims_own"] = _answer.count_claims(ok[0].driving_fact, ok[0].reason)
        out.append(card)
    return out


def field_markdown(card, key: str) -> str:
    """One card field as Markdown. Side by side (an unstable judge) each side is marked on its own -
    "HOLD: *claim* `own knowledge` | BUY: claim." - so no italic or tag runs across the " | "."""
    sides = card.get("sides", {}).get(key)
    if sides:
        return " | ".join(f"{v}: {_mark_field_markdown(key, t)}" for v, t in sides)
    return _mark_field_markdown(key, card[key])


def field_html(card, key: str) -> str:
    sides = card.get("sides", {}).get(key)
    if sides:
        return " | ".join(f"{_html.escape(v)}: {_mark_field_html(key, t)}" for v, t in sides)
    return _mark_field_html(key, card[key])


def _mark_field_markdown(key: str, text: str) -> str:
    return text if key in PLAIN_FIELDS else mark_markdown(text)


def _mark_field_html(key: str, text: str) -> str:
    return _html.escape(text) if key in PLAIN_FIELDS else mark_html(text)


def _lens_votes(record) -> str:
    """The lenses' votes as '0 BUY, 2 HOLD, 0 SELL' - never judge counts."""
    return ", ".join(f"{n} {w}" for w, n in _split_pairs(record.split))


def _split_pairs(split: str):
    parts = [p.split() for p in (split or "").split(" / ")]
    return [(p[0], p[1]) for p in parts if len(p) == 2]


def summary_lines(result: PanelResult) -> list[str]:
    """ONE line: the verdict of record with the lenses' votes in the bracket, then the panel's split
    (judges, not calls) and whether it agrees. e.g. "Verdict of record HOLD (lens votes: 0 BUY,
    2 HOLD, 0 SELL). Panel: 3 HOLD — agrees." """
    from .panel import AGREES, DISAGREES, MIXED, NO_READING, NO_STABLE

    rec = result.record
    panel = f"Panel: {result.split_line}"
    if not rec.verdict:                    # no lens voted: there is nothing to agree or disagree with
        line = f"Verdict of record: none (no lens voted). {panel}."
        if result.agreement == NO_READING:
            line += " No panel reading: fewer than two judges answered."
        return [line]
    head = f"Verdict of record {rec.verdict} (lens votes: {_lens_votes(rec)})."
    tail = {AGREES: "agrees", DISAGREES: "disagrees", MIXED: "mixed against the record"}.get(result.agreement)
    if tail:
        return [f"{head} {panel} — {tail}."]
    if result.agreement == NO_READING:
        return [f"{head} {panel}. No panel reading: fewer than two judges answered."]
    if result.agreement == NO_STABLE:
        return [f"{head} {panel}. The judges that answered all abstained, so there is nothing to compare."]
    return [f"{head} {panel}."]


def reading_text(result: PanelResult) -> tuple[str, str]:
    """``(paragraph, note)``: the narrator's paragraph, or why there is none when one was due."""
    rd = result.reading
    if rd is None:
        return "", ""
    return rd.paragraph, ("" if rd.paragraph or not needs_reading(result.agreement) else rd.note)


def unexplained_line(result) -> str:
    """"The judge panel could not account for: ..." for the story's "What to doubt" paragraph, or ""
    when the panel did not run or nobody had anything unexplained. One line; each judge once."""
    if result is None or not getattr(result, "available", False):
        return ""
    items = []
    for j in result.judges:
        c = j.first_ok if j.status == STABLE else None
        text = _answer.strip_tags((c.unexplained if c else "").strip()).strip()
        if text and text.lower().rstrip(".") not in ("nothing", "none"):
            items.append(f"{j.label}: {text}")
    if not items:
        return ""
    # DOUBLE-STOP-1: a gap sentence may already end with its own stop (or "?", or a stop inside a closing
    # quote). Between items the stop is dropped for the "; "; at the end the line takes ONE stop unless
    # the last sentence already has one.
    items = [re.sub(r"\.+$", "", t) for t in items[:-1]] + [re.sub(r"\.{2,}$", ".", items[-1])]
    line = "The judge panel could not account for: " + "; ".join(items)
    return line if re.search(r"[.!?][\"”’)\]]*$", line) else line + "."


def _head(card) -> str:
    conf = f" (confidence {card['confidence']})" if card["confidence"] else ""
    return f"{card['judge']}: {card['verdict']}{conf}"


def text_lines(result: PanelResult) -> list[str]:
    """Plain-text block for the .txt download."""
    if not result.available:
        return [f"  Not run: {result.note}"]
    out = []
    for card in cards(result):
        out.append("  " + _head(card))
        if claims_line(card):
            out.append(f"      {claims_line(card)}")
        for key, caption in FIELDS:
            if card[key]:
                out.append(f"      {caption.lower()}: {mark_text(card[key])}")
    out += ["  " + ln for ln in summary_lines(result)]
    paragraph, note = reading_text(result)
    if paragraph:
        out += ["", "  " + paragraph, f"  ({READING_CAPTION})"]
    elif note:
        out += ["", "  " + note]
    out += ["", f"  ({own_tag_note(result)})"]
    return out


def markdown_lines(result: PanelResult) -> list[str]:
    out = [f"## {SECTION_TITLE}", "", f"_{CAPTION}_", ""]
    if not result.available:
        return out + [f"Not run: {result.note}", ""]
    for card in cards(result):
        out += [f"**{_head(card)}**", ""]
        if claims_line(card):
            out.append(f"- *{claims_line(card)}*")
        for key, caption in FIELDS:
            if card[key]:
                out.append(f"- *{caption}:* {field_markdown(card, key)}")
        out.append("")
    out += [*summary_lines(result), ""]
    paragraph, note = reading_text(result)
    if paragraph:
        out += [paragraph, "", f"_{READING_CAPTION}_", ""]
    elif note:
        out += [f"_{note}_", ""]
    out += [f"_{own_tag_note(result)}_", ""]
    return out


def html_section(result: PanelResult, esc=_html.escape) -> str:
    """The section for the HTML download."""
    out = [f'<section class="section"><h2>{esc(SECTION_TITLE)}</h2>', f'<p class="note">{esc(CAPTION)}</p>']
    if not result.available:
        return "".join(out) + f'<p class="note">Not run: {esc(result.note)}</p></section>'
    for card in cards(result):
        out.append(f'<div class="judge"><p><strong>{esc(_head(card))}</strong></p>')
        extra = f"<li><em>{esc(claims_line(card))}</em></li>" if claims_line(card) else ""
        items = extra + "".join(f"<li><em>{esc(caption)}:</em> {field_html(card, key)}</li>"
                        for key, caption in FIELDS if card[key])
        if items:
            out.append(f"<ul>{items}</ul>")
        out.append("</div>")
    out += [f"<p>{esc(line)}</p>" for line in summary_lines(result)]
    paragraph, note = reading_text(result)
    if paragraph:
        out.append(f'<p>{esc(paragraph)}</p><p class="note">{esc(READING_CAPTION)}</p>')
    elif note:
        out.append(f'<p class="note">{esc(note)}</p>')
    out.append(f'<p class="note">{esc(own_tag_note(result))}</p></section>')
    return "".join(out)
