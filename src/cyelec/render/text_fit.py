"""Loss-free text fitting helpers shared by every CyElec drawing renderer (SVG + DXF).

Drafting invariant enforced by these helpers: **no label is ever truncated or ellipsised**.
Long strings are word-wrapped onto additional lines (and, where a box has a hard line
budget, the font is stepped down to a legible floor before the box is grown), so every
character of user/project/engineering text reaches the issued sheet.

Additionally `hoist_svg_text_to_top` re-orders top-level `<text>` elements so they are
painted after all symbols, wires and fills - i.e. no drawing shape can ever be painted
over a label (combined with the white `paint-order: stroke` halos this is the SVG
equivalent of an AutoCAD text background mask / WIPEOUT).
"""

from __future__ import annotations

import re

from cyelec.render._glyph_metrics import DEJAVU_BOLD_EM, DEJAVU_REGULAR_EM
from cyelec.render.label_layout import estimate_text_size

_TEXT_LINE_RE = re.compile(r"^\s*<text\b[^>]*>.*</text>\s*$")
_MASK_LINE_RE = re.compile(r'^\s*<rect class="txt-mask[ "][^>]*/>\s*$')
_BREAK_AFTER = (" ", "/", "-", ",", "|", "+", "&", ")", ";", ":")


def text_width_px(text: str, font_px: float, *, bold: bool = False) -> float:
    """Rendered width (px) of `text` at `font_px` from real DejaVu Sans advance widths.

    Characters outside the bundled table fall back to the conservative generic estimator.
    A 2 % safety margin covers kerning/hinting differences between SVG/PDF rasterisers.
    """
    if not text:
        return 0.0
    table = DEJAVU_BOLD_EM if bold else DEJAVU_REGULAR_EM
    em = 0.0
    for ch in text:
        w = table.get(ch)
        em += w if w is not None else estimate_text_size(ch, 1.0, bold=bold)[0] * 1.1
    return em * font_px * 1.02


def _tokens(text: str) -> list[str]:
    """Split into wrap tokens, keeping break characters attached to the preceding token."""
    out: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if ch in _BREAK_AFTER:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def _hard_split(token: str, max_w: float, font_px: float, bold: bool) -> list[str]:
    """Split a single unbreakable token by characters so each piece fits `max_w`."""
    pieces: list[str] = []
    cur = ""
    for ch in token:
        if cur and text_width_px(cur + ch, font_px, bold=bold) > max_w:
            pieces.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        pieces.append(cur)
    return pieces


def wrap_text_to_width(
    text: str, max_w: float, font_px: float, *, bold: bool = False
) -> list[str]:
    """Word-wrap `text` into lines no wider than `max_w` px. Never drops a character."""
    text = " ".join(str(text).split())
    if not text:
        return [""]
    if text_width_px(text, font_px, bold=bold) <= max_w:
        return [text]
    lines: list[str] = []
    cur = ""
    for tok in _tokens(text):
        cand = cur + tok
        if text_width_px(cand.rstrip(), font_px, bold=bold) <= max_w:
            cur = cand
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        tok_s = tok.lstrip()
        if text_width_px(tok_s.rstrip(), font_px, bold=bold) > max_w:
            pieces = _hard_split(tok_s, max_w, font_px, bold)
            lines.extend(p.rstrip() for p in pieces[:-1])
            cur = pieces[-1]
        else:
            cur = tok_s
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [text]


def wrap_text_chars(text: str, max_chars: int) -> list[str]:
    """Character-budget word wrap (for DXF TEXT where glyph metrics are font-agnostic)."""
    text = " ".join(str(text).split())
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    cur = ""
    for tok in _tokens(text):
        if len((cur + tok).rstrip()) <= max_chars:
            cur += tok
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        tok_s = tok.lstrip()
        while len(tok_s.rstrip()) > max_chars:
            lines.append(tok_s[:max_chars])
            tok_s = tok_s[max_chars:]
        cur = tok_s
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [text]


def dxf_char_budget(width_mm: float, height_mm: float) -> int:
    """Conservative DXF TEXT character budget for `width_mm` at text height `height_mm`.

    CAD fonts (txt.shx / Arial / ISOCP) average <= ~0.9 x height per glyph incl. spacing;
    0.95 keeps wrapped lines inside their frame for every common SHX/TTF mapping.
    """
    return max(8, int(width_mm / (height_mm * 0.95)))


def fit_text_lines(
    text: str,
    max_w: float,
    font_px: float,
    *,
    max_lines: int,
    min_font_px: float,
    bold: bool = False,
    step: float = 0.2,
) -> tuple[list[str], float]:
    """Wrap `text` into <= `max_lines` lines, stepping the font down to `min_font_px` if needed.

    If the text still needs more lines at the legibility floor, ALL lines are returned at
    `min_font_px` (the caller must grow its container) - content is never discarded.
    """
    fs = font_px
    while True:
        lines = wrap_text_to_width(text, max_w, fs, bold=bold)
        if len(lines) <= max_lines or fs - step < min_font_px - 1e-9:
            return lines, round(fs, 2)
        fs -= step


def fit_single_line_font(
    text: str, max_w: float, font_px: float, *, min_font_px: float, bold: bool = False
) -> float:
    """Largest font <= `font_px` (>= `min_font_px`) at which `text` fits on one line."""
    w = text_width_px(text, font_px, bold=bold)
    if w <= max_w:
        return font_px
    return max(min_font_px, round(font_px * max_w / max(w, 1e-6) - 0.05, 2))


def hoist_svg_text_to_top(svg: str) -> str:
    """Move every top-level single-line `<text>` element to just before `</svg>`.

    Relative order among texts is preserved. Only used on flat (group-free) sheets where
    text coordinates are absolute, so re-ordering never changes geometry - it only makes
    labels paint above wires, symbols and fills. Single-line `<rect class="txt-mask">`
    background masks (the SVG equivalent of an AutoCAD MTEXT background mask) are hoisted
    too and emitted *before* all texts, so a mask can hide geometry but never another label.
    """
    lines = svg.split("\n")
    body: list[str] = []
    masks: list[str] = []
    texts: list[str] = []
    close_idx = -1
    for ln in lines:
        if ln.strip() == "</svg>":
            close_idx = len(body)
            body.append(ln)
            continue
        if _TEXT_LINE_RE.match(ln):
            texts.append(ln)
        elif _MASK_LINE_RE.match(ln):
            masks.append(ln)
        else:
            body.append(ln)
    if close_idx < 0:
        return svg
    return "\n".join(body[:close_idx] + masks + texts + body[close_idx:])


def svg_text_mask(
    text: str,
    x: float,
    baseline_y: float,
    font_px: float,
    *,
    anchor: str = "start",
    bold: bool = False,
    pad: float = 1.6,
) -> str:
    """Opaque white background-mask rect sized to the real DejaVu ink box of `text`."""
    w = text_width_px(text, font_px, bold=bold) + 2 * pad
    if anchor == "middle":
        x0 = x - w / 2
    elif anchor == "end":
        x0 = x - w + pad
    else:
        x0 = x - pad
    y0 = baseline_y - 0.96 * font_px - pad * 0.6
    h = 1.22 * font_px + pad * 1.2
    return (
        f'  <rect class="txt-mask" x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" '
        f'height="{h:.1f}" fill="#ffffff" fill-opacity="0.92" style="pointer-events:none;" />'
    )
