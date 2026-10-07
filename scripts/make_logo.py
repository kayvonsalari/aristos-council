"""One-off DEV tool (not a project dependency): build assets/aristos_logo.svg (+ _light) and aristos_mark.svg.

The capital "A" (Cinzel 700) and the wordmark "Aristos" (Cinzel 600, letter-spacing .08em) are converted
to outlines, so the logo never depends on a font loading. Needs `pip install fonttools` and the Cinzel
variable font (github.com/google/fonts/ofl/cinzel). Usage:

    python scripts/make_logo.py path/to/Cinzel[wght].ttf assets
"""
import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ACCENT, INK, TEXT, TEXT_LIGHT = "#7FB2FF", "#0E1217", "#E6EAF0", "#16202C"


def _fmt(v: float) -> str:
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _instance(ttf: str, wght: int):
    return instantiateVariableFont(TTFont(ttf), {"wght": wght})


def _glyph_path(font, ch: str, scale: float, tx: float, ty: float) -> str:
    gs = font.getGlyphSet()
    name = font.getBestCmap()[ord(ch)]
    pen = SVGPathPen(gs, ntos=_fmt)
    gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, tx, ty)))
    return pen.getCommands()


def _centred_a(font, size: float, cx: float, cy: float) -> str:
    gs = font.getGlyphSet()
    name = font.getBestCmap()[ord("A")]
    bp = BoundsPen(gs)
    gs[name].draw(bp)
    x0, y0, x1, y1 = bp.bounds
    s = size / (y1 - y0)
    return _glyph_path(font, "A", s, cx - (x0 + x1) / 2 * s, cy + (y0 + y1) / 2 * s)


def _wordmark(font, text: str, px: float, x: float, baseline: float, spacing_em: float = 0.08) -> str:
    s = px / font["head"].unitsPerEm
    hmtx = font["hmtx"]
    cmap = font.getBestCmap()
    d, pen_x = [], x
    for ch in text:
        d.append(_glyph_path(font, ch, s, pen_x, baseline))
        pen_x += hmtx[cmap[ord(ch)]][0] * s + spacing_em * px
    return " ".join(d), pen_x - spacing_em * px


def main(ttf: str, out: str) -> None:
    out_dir = Path(out)
    bold, semi = _instance(ttf, 700), _instance(ttf, 600)
    a32 = _centred_a(bold, 17.0, 16.0, 16.0)
    mark = (f'<rect width="32" height="32" rx="9" fill="{ACCENT}"/>'
            f'<path d="{a32}" fill="{INK}"/>')
    word, end = _wordmark(semi, "Aristos", 21.0, 44.0, 23.0)
    width = round(end + 2)
    (out_dir / "aristos_mark.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 32 32" '
        f'role="img" aria-label="Aristos">{mark}</svg>\n', encoding="utf-8")
    for name, ink in (("aristos_logo.svg", TEXT), ("aristos_logo_light.svg", TEXT_LIGHT)):
        (out_dir / name).write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="32" '
            f'viewBox="0 0 {width} 32" role="img" aria-label="Aristos">{mark}'
            f'<path d="{word}" fill="{ink}"/></svg>\n', encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
