"""UI-POLISH-1 - the look of the Analyse page, in ONE place.

Look and branding only: nothing here computes, ranks or words a verdict. It holds

* the palette (a dark one, the product's, and a light one for a user who switches Streamlit to
  Light in Settings - every text/background pair is checked for 4.5:1 contrast by a test),
* ONE CSS block, injected once per script run by ``inject()``,
* small pure builders that return HTML strings (verdict chips, outlined badges, stat cards, the
  cheapest-to-dearest bar, a scrolling table, the empty-state panel).

Every verdict chip carries its WORD (BUY / HOLD / SELL / does not apply ...): colour is never the only
signal. Builders escape every string they are handed and turn "$" into a character reference, because
Streamlit's markdown would otherwise read two dollar signs as maths (DOLLAR-MATH-1).

STREAMLIT INTERNALS: Streamlit updates can break custom CSS, so the block uses our own ``ar-`` classes
wherever it can and only these Streamlit hooks, each commented where it appears in ``css()``:
``.stApp`` (page background and font), ``[data-testid="stSidebar"]`` (sidebar font),
``[data-testid="stHeader"]`` (the top bar), ``[data-testid="stMainBlockContainer"]`` (top padding),
``[data-testid="stHeaderActionElements"]`` (heading link icons), ``[data-testid="stElementContainer"]``
(our own style blocks), and ``[data-testid="stMarkdownContainer"]``
(table text size). Nothing else of Streamlit's is restyled; the colours themselves come from
``.streamlit/config.toml``, which is Streamlit's supported theming route.
"""
from __future__ import annotations

import html
import re
from typing import Iterable, Optional

PRODUCT = "Aristos"

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #
DARK = {
    "bg": "#0E1217", "panel": "#161D26", "border": "#263241", "text": "#E6EAF0",
    "muted": "#8B97A8", "accent": "#7FB2FF",
    "buy_fg": "#6FD3AA", "buy_bg": "rgba(63,182,139,.16)",
    "hold_fg": "#EBC46E", "hold_bg": "rgba(230,178,70,.16)",
    "sell_fg": "#F2858A", "sell_bg": "rgba(229,72,77,.18)",
    "na_fg": "#B7C2D0", "na_bg": "#1C2530",
    # B22-U6: cheap is not "good" - the valuation bar is blue -> grey -> orange, never green/amber/red
    "bar_a": "#2E5C8F", "bar_b": "#263241", "bar_c": "#8F5A2E",
}
LIGHT = {
    "bg": "#F5F7FA", "panel": "#FFFFFF", "border": "#D3DBE6", "text": "#16202C",
    "muted": "#526074", "accent": "#1D5FC0",
    "buy_fg": "#0A6B46", "buy_bg": "rgba(34,150,100,.14)",
    "hold_fg": "#7A5300", "hold_bg": "rgba(210,150,20,.18)",
    "sell_fg": "#A3202A", "sell_bg": "rgba(210,50,60,.13)",
    "na_fg": "#3F4B5B", "na_bg": "#E8EDF3",
    "bar_a": "#2E5C8F", "bar_b": "#C3CCD8", "bar_c": "#B8742E",
}
PALETTES = {"dark": DARK, "light": LIGHT}

# verdict word (any case, either vocabulary) -> chip kind. Check marks and "does not apply" are neutral.
_KIND = {"buy": "buy", "hold": "hold", "sell": "sell"}


def chip_kind(word: str) -> str:
    return _KIND.get((word or "").strip().lower(), "na")


# --------------------------------------------------------------------------- #
# escaping
# --------------------------------------------------------------------------- #
def esc(text) -> str:
    """HTML-escape ``text`` and make "$" safe from Streamlit's maths parser (a character reference)."""
    return html.escape(str(text if text is not None else ""), quote=True).replace("$", "&#36;")


def _one_line(markup: str) -> str:
    """Streamlit's markdown turns a line indented four spaces (or a blank line inside a block) into
    something else, so every builder emits ONE unindented line."""
    return re.sub(r">\s+<", "><", markup.replace("\n", "")).strip()


# --------------------------------------------------------------------------- #
# theme
# --------------------------------------------------------------------------- #
def theme_name() -> str:
    """"dark" or "light": the Streamlit theme in force, dark when unknown.

    Read from the CONFIGURED base (``theme.base``: ``.streamlit/config.toml`` says dark, a launch flag
    such as ``--theme.base light`` says light). ``st.context.theme`` is deliberately not used first: in a
    browser whose own preference is light it reported "light" on top of our dark config, which painted
    white cards on a dark page. It is only the fallback when no base is configured."""
    try:
        import streamlit as st
        base = str(st.get_option("theme.base") or "").lower()
        if base in PALETTES:
            return base
        kind = str(st.context.theme.type or "").lower()
        return kind if kind in PALETTES else "dark"
    except Exception:                                    # noqa: BLE001 - no runtime, old Streamlit
        return "dark"


# --------------------------------------------------------------------------- #
# the one CSS block
# --------------------------------------------------------------------------- #
# U1 (Batch 22): numbers were drawn in a Courier-like fallback because IBM Plex Mono never arrived (a
# second family in the import, fetched lazily). Numbers now use IBM Plex Sans with tabular figures: one
# family, one request, aligned digits.
FONT_IMPORT = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600"
               "&display=swap")
SANS = "'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"


def css(theme: str = "dark") -> str:
    p = PALETTES.get(theme, DARK)
    return f"""
@import url('{FONT_IMPORT}');
:root {{
  --ar-bg: {p['bg']}; --ar-panel: {p['panel']}; --ar-border: {p['border']}; --ar-text: {p['text']};
  --ar-muted: {p['muted']}; --ar-accent: {p['accent']};
  --ar-buy-fg: {p['buy_fg']}; --ar-buy-bg: {p['buy_bg']};
  --ar-hold-fg: {p['hold_fg']}; --ar-hold-bg: {p['hold_bg']};
  --ar-sell-fg: {p['sell_fg']}; --ar-sell-bg: {p['sell_bg']};
  --ar-na-fg: {p['na_fg']}; --ar-na-bg: {p['na_bg']};
}}
/* STREAMLIT INTERNAL: .stApp is the page root; the font is set here and inherited. Icons keep their own
   font because Streamlit sets it on the icon element itself. */
.stApp, [data-testid="stSidebar"] {{ font-family: {SANS}; }}
/* B22-U7 / U9. STREAMLIT INTERNALS (each hook is a data-testid Streamlit sets; re-check after an upgrade):
   - stHeaderActionElements: the link icon Streamlit puts beside every heading - hidden.
   - stHeader: the top bar. It is made transparent and zero-height so it neither paints a band nor
     intercepts clicks; its sidebar toggle and status widget still render (overflow stays visible).
   - stMainBlockContainer: the main column; its default 6rem top padding is cut so the Analyse /
     Scoreboard tabs sit level with the ARISTOS logo row (measured: both at y=12-14px, 400-1300px wide).
   - the element containers holding our own <style> blocks: each takes a 1rem layout gap, so they are
     taken out of the layout (the CSS inside still applies). */
[data-testid="stHeaderActionElements"] {{ display: none !important; }}
[data-testid="stHeader"] {{ background: transparent !important; height: 0 !important; min-height: 0 !important; }}
[data-testid="stMainBlockContainer"] {{ padding-top: .75rem !important; }}
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style) {{ display: none; }}
.ar-num, .ar-table td.ar-num {{ font-variant-numeric: tabular-nums; font-feature-settings: 'tnum'; }}
.ar-muted {{ color: var(--ar-muted); }}

/* verdict chips: a pill that ALWAYS carries its word */
.ar-chip {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem;
  font-weight: 600; letter-spacing: .02em; line-height: 1.5; white-space: nowrap; }}
.ar-chip-buy {{ color: var(--ar-buy-fg); background: var(--ar-buy-bg); }}
.ar-chip-hold {{ color: var(--ar-hold-fg); background: var(--ar-hold-bg); }}
.ar-chip-sell {{ color: var(--ar-sell-fg); background: var(--ar-sell-bg); }}
.ar-chip-na {{ color: var(--ar-na-fg); background: var(--ar-na-bg); font-weight: 500; }}
.ar-badge {{ display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: .72rem;
  color: var(--ar-muted); border: 1px solid var(--ar-border); white-space: nowrap; }}

/* cards */
.ar-card {{ background: var(--ar-panel); border: 1px solid var(--ar-border); border-radius: 12px;
  padding: 16px 18px; color: var(--ar-text); }}
.ar-header {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px 24px;
  align-items: flex-start; margin: 4px 0 10px; }}
.ar-name {{ font-size: 1.7rem; font-weight: 600; line-height: 1.2; }}
.ar-sub {{ color: var(--ar-muted); font-size: .88rem; margin-top: 4px; }}
.ar-price {{ text-align: right; }}
.ar-price .ar-big {{ font-size: 1.5rem; }}
.ar-chiprow {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }}
.ar-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px;
  margin: 8px 0 18px; }}
.ar-stat-title {{ color: var(--ar-muted); font-size: .76rem; text-transform: uppercase;
  letter-spacing: .06em; }}
.ar-big {{ font-size: 1.15rem; font-weight: 500; margin: 6px 0 2px; }}
.ar-stat-sub {{ color: var(--ar-muted); font-size: .8rem; line-height: 1.45; }}
.ar-abstain {{ color: var(--ar-muted); font-size: .86rem; margin-top: 8px; }}
.ar-bar {{ position: relative; display: flex; height: 6px; margin: 12px 4px 4px; gap: 2px; }}
.ar-bar i {{ flex: 1; border-radius: 3px; opacity: .85; }}
.ar-bar i:nth-child(1) {{ background: {p['bar_a']}; }}
.ar-bar i:nth-child(2) {{ background: {p['bar_b']}; }}
.ar-bar i:nth-child(3) {{ background: {p['bar_c']}; }}
.ar-bar-mark {{ position: absolute; top: -4px; width: 3px; height: 14px; border-radius: 2px;
  background: var(--ar-text); transform: translateX(-50%); }}
.ar-bar-ends {{ display: flex; justify-content: space-between; color: var(--ar-muted); font-size: .7rem; }}

/* tables: scroll sideways INSIDE their box, never the page */
.ar-table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border: 1px solid var(--ar-border);
  border-radius: 12px; background: var(--ar-panel); margin: 6px 0 12px; }}
.ar-table {{ width: 100%; border-collapse: collapse; color: var(--ar-text); font-size: .9rem; }}
.ar-table th {{ text-align: left; color: var(--ar-muted); font-weight: 500; font-size: .76rem;
  text-transform: uppercase; letter-spacing: .05em; padding: 10px 14px;
  border-bottom: 1px solid var(--ar-border); white-space: nowrap; }}
.ar-table td {{ padding: 10px 14px; border-bottom: 1px solid var(--ar-border); vertical-align: top; }}
.ar-table tr:last-child td {{ border-bottom: 0; }}
.ar-table .ar-reason {{ color: var(--ar-muted); font-size: .8rem; min-width: 220px; }}
.ar-table .ar-detail {{ color: var(--ar-muted); font-size: .8rem; margin-left: 8px; }}

/* AI text check: a small amber marker at the end of a flagged sentence. The reason shows on hover (desktop)
   and on focus, i.e. a tap (phone); it is also the title and the aria-label. */
.ar-flag {{ position: relative; display: inline-block; margin-left: 4px; cursor: help; outline: none;
  color: var(--ar-hold-fg); background: var(--ar-hold-bg); border-radius: 999px; padding: 0 6px;
  font-size: .72rem; line-height: 1.5; vertical-align: baseline; }}
.ar-flag .ar-flag-tip {{ display: none; position: absolute; z-index: 20; left: 0; top: 130%;
  min-width: 220px; max-width: 320px; white-space: normal; padding: 8px 10px; border-radius: 8px;
  background: var(--ar-panel); color: var(--ar-text); border: 1px solid var(--ar-border);
  font-size: .8rem; line-height: 1.4; font-weight: 400; box-shadow: 0 4px 14px rgba(0,0,0,.25); }}
.ar-flag:hover .ar-flag-tip, .ar-flag:focus .ar-flag-tip {{ display: block; }}

/* start state */
.ar-empty {{ text-align: center; padding: 44px 20px; margin: 18px 0; }}
.ar-empty h3 {{ margin: 0 0 8px; font-weight: 600; }}
.ar-empty p {{ color: var(--ar-muted); max-width: 560px; margin: 0 auto 16px; }}
.ar-steps {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-bottom: 16px; }}
.ar-step {{ border: 1px solid var(--ar-border); border-radius: 999px; padding: 4px 14px;
  font-size: .84rem; color: var(--ar-text); }}
.ar-hint {{ color: var(--ar-muted); font-size: .84rem; }}

/* phone width: cards stack (the grid already wraps), the header lets the price drop under the name */
@media (max-width: 640px) {{
  [data-testid="stMainBlockContainer"] {{ padding-top: 3rem !important; }}   /* room for the sidebar toggle */
  .ar-name {{ font-size: 1.35rem; }}
  .ar-price {{ text-align: left; }}
  .ar-card {{ padding: 14px; }}
}}
"""


def inject() -> None:
    """Put the CSS on the page. Called once per script run from ``main()``."""
    import streamlit as st
    st.markdown(f"<style>{css(theme_name())}</style>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# builders (pure: strings in, one-line HTML out)
# --------------------------------------------------------------------------- #
def chip(word: str, kind: Optional[str] = None) -> str:
    """A pill carrying ``word``; ``kind`` defaults from the word (BUY/HOLD/SELL, else neutral)."""
    return f'<span class="ar-chip ar-chip-{kind or chip_kind(word)}">{esc(word)}</span>'


def badge(label: str) -> str:
    """A small outlined label (a track-record badge)."""
    return f'<span class="ar-badge">{esc(label)}</span>' if label else ""


def chip_row(chips: Iterable[str]) -> str:
    items = [c for c in chips if c]
    return f'<div class="ar-chiprow">{"".join(items)}</div>' if items else ""


def stat_card(title: str, big: str = "", sub: Iterable[str] = (), *, abstain: str = "",
              extra_html: str = "") -> str:
    """One small card. A figure that is missing is NEVER a blank or a zero: pass ``abstain`` (a short
    muted reason) and ``big`` stays empty."""
    head = f'<div class="ar-stat-title">{esc(title)}</div>'
    if big and not abstain:
        body = f'<div class="ar-big ar-num">{esc(big)}</div>' + extra_html
        body += "".join(f'<div class="ar-stat-sub">{esc(s)}</div>' for s in sub if s)
    else:
        body = f'<div class="ar-abstain">{esc(abstain or "not available")}</div>'
    return _one_line(f'<div class="ar-card">{head}{body}</div>')


def cards_grid(cards: Iterable[str]) -> str:
    return _one_line(f'<div class="ar-cards">{"".join(cards)}</div>')


def percentile_bar(percentile: float) -> str:
    """The thin cheapest-to-dearest bar with a marker at ``percentile`` (0-100)."""
    p = max(0.0, min(100.0, float(percentile)))
    return _one_line(f'<div class="ar-bar"><i></i><i></i><i></i><div class="ar-bar-mark" style="left:{p:.1f}%"></div></div>'
                     '<div class="ar-bar-ends"><span>cheapest</span><span>dearest</span></div>')


def table(headers: list[str], rows: list[list[str]], *, raw_cols: Iterable[int] = (),
          num_cols: Iterable[int] = (), reason_col: Optional[int] = None) -> str:
    """A scrolling-inside-its-box table. Cells in ``raw_cols`` are already-built HTML (chips); every
    other cell is escaped. ``num_cols`` use the tabular-figure style."""
    raw, nums = set(raw_cols), set(num_cols)
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for r in rows:
        tds = []
        for i, cell in enumerate(r):
            cls = " ".join(c for c in ("ar-num" if i in nums else "",
                                       "ar-reason" if i == reason_col else "") if c)
            tds.append(f'<td{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>'
                       f'{cell if i in raw else esc(cell)}</td>')
        body.append(f'<tr>{"".join(tds)}</tr>')
    return _one_line(f'<div class="ar-table-wrap"><table class="ar-table"><thead><tr>{head}</tr></thead>'
                     f'<tbody>{"".join(body)}</tbody></table></div>')


def empty_panel(title: str, sentence: str, steps: Iterable[str], hint: str) -> str:
    chips = "".join(f'<span class="ar-step">{esc(s)}</span>' for s in steps)
    return _one_line(f'<div class="ar-card ar-empty"><h3>{esc(title)}</h3><p>{esc(sentence)}</p>'
                     f'<div class="ar-steps">{chips}</div><div class="ar-hint">{esc(hint)}</div></div>')


# --------------------------------------------------------------------------- #
# contrast (used by the tests, and by anyone adding a colour)
# --------------------------------------------------------------------------- #
def _rgb(colour: str) -> tuple[float, float, float, float]:
    colour = colour.strip()
    m = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", colour)
    if m:
        return float(m[1]), float(m[2]), float(m[3]), float(m[4])
    h = colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0


def over(fg: str, bg: str) -> tuple[float, float, float]:
    """``fg`` (maybe translucent) composited onto the opaque ``bg``."""
    r, g, b, a = _rgb(fg)
    br, bg_, bb, _ = _rgb(bg)
    return (r * a + br * (1 - a), g * a + bg_ * (1 - a), b * a + bb * (1 - a))


def _lum(c: tuple[float, float, float]) -> float:
    def ch(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(fg: str, bg: str, *, under: Optional[str] = None) -> float:
    """WCAG contrast of ``fg`` on ``bg``; a translucent ``bg`` is first composited onto ``under``."""
    bg_rgb = over(bg, under) if (under and _rgb(bg)[3] < 1) else _rgb(bg)[:3]
    hi, lo = sorted((_lum(_rgb(fg)[:3]), _lum(bg_rgb)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
