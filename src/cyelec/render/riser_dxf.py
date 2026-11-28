"""E-202 Vertical Riser Diagram & Physical 18 mm DIN-Rail DB Elevation + Selectivity (DXF + SVG)."""

from __future__ import annotations

from html import escape as esc
from pathlib import Path
from typing import Any

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import DinRailBoardLayout, ElectricalDesignResult
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.text_fit import (
    dxf_char_budget,
    fit_text_lines,
    wrap_text_chars,
    wrap_text_to_width,
)


def _unwrap(result: Phase2DesignResult | ElectricalDesignResult) -> ElectricalDesignResult:
    return result.design if isinstance(result, Phase2DesignResult) else result


def _dxf_box_text(
    msp: Any, text: str, x: float, mid_y: float, width: float, height: float, layer: str
) -> None:
    """Riser box label on <= 2 lines (height stepped down to 1.4 mm), then wrapped - never cut."""
    h = height
    lines = wrap_text_chars(text, dxf_char_budget(width, h))
    while len(lines) > 2 and h > 1.4:
        h = round(h - 0.1, 2)
        lines = wrap_text_chars(text, dxf_char_budget(width, h))
    lh = h * 1.35
    y0 = mid_y - h / 2 + (len(lines) - 1) * lh / 2
    for i, ln in enumerate(lines):
        msp.add_text(ln, dxfattribs={"layer": layer, "height": h}).set_placement(
            (x, y0 - i * lh)
        )


def _dxf_card_lines(brd: DinRailBoardLayout) -> _CardText:
    head = wrap_text_chars(
        f"{brd.board_id} ({brd.floor}) — {brd.total_modules_used}M / "
        f"{brd.total_modules_capacity}M ({brd.spare_pct:.0f}% spare)",
        dxf_char_budget(190.0, 2.4),
    )
    encl = wrap_text_chars(f"Enclosure: {brd.enclosure_spec}", dxf_char_budget(190.0, 1.9))
    sel = wrap_text_chars(
        f"Ipf={brd.incoming_ipf_ka:.1f}kA <= Icu={brd.min_device_icu_ka:.0f}kA | "
        f"{brd.selectivity_note}",
        dxf_char_budget(190.0, 1.9),
    )
    rows = [
        wrap_text_chars(
            _row_label(r.row_no, r.modules_used, r.modules_capacity, r.items),
            dxf_char_budget(184.0, 1.7),
        )
        for r in brd.rows
    ]
    return head, encl, sel, rows


def _dxf_card_height(card: _CardText) -> float:
    head, encl, sel, rows = card
    h = len(head) * 4.0 + 1.8 + len(encl) * 3.2 + 2.0 + len(sel) * 3.2 + 3.3
    for rl in rows:
        h += len(rl) * 2.6 + 3.5 + 3.6
    return h + 4.5


def render_riser_and_db_dxf(result: Phase2DesignResult | ElectricalDesignResult) -> Drawing:
    """Build pristine R2018 DXF E-202 Vertical Riser & Physical 18mm DIN-Rail DB Layout."""
    design = _unwrap(result)
    b = design.building
    eac = design.eac_compliance
    boards = design.din_rail_boards

    doc = new_dxf("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1

    for lname, color in (
        ("E-TITLE", 7),
        ("E-RISER-PWR", 1),
        ("E-RISER-ELV", 5),
        ("E-RISER-EARTHING", 3),
        ("E-RISER-PV", 40),
        ("E-DIN-RAIL", 4),
        ("E-DIN-SPARE", 8),
    ):
        if lname not in doc.layers:
            doc.layers.add(lname, color=color)

    msp = doc.modelspace()
    w_tot = 420.0
    title_lines = wrap_text_chars(
        f"E-202: VERTICAL RISER DIAGRAM & PHYSICAL 18MM DIN-RAIL DB LAYOUT — {b.project_name}",
        dxf_char_budget(404.0, 4.0),
    )
    title_extra = (len(title_lines) - 1) * 5.6
    # Right-pane schedule is fully listed (all boards / rows / items) - size the sheet to it.
    dxf_cards = [_dxf_card_lines(brd) for brd in boards]
    pane_h = sum(_dxf_card_height(c) for c in dxf_cards)
    h_tot = max(290.0, 32.0 + pane_h + 12.0) + title_extra
    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    for i, t_line in enumerate(title_lines):
        msp.add_text(t_line, dxfattribs={"layer": "E-TITLE", "height": 4.0}).set_placement(
            (8.0, h_tot - 10.0 - i * 5.6)
        )
    h_tot -= title_extra  # body coordinates below the (possibly multi-line) title band
    msp.add_line((0.0, h_tot - 14.0), (w_tot, h_tot - 14.0), dxfattribs={"layer": "E-TITLE"})

    # Left Pane (x=8..205): Multi-Level Vertical Riser Schematic (SITE -> GF -> 1F -> ... -> ROOF)
    msp.add_line((208.0, 6.0), (208.0, h_tot - 14.0), dxfattribs={"layer": "E-TITLE"})
    msp.add_text(
        "A. VERTICAL RISER DIAGRAM (POWER, ELV, PV & EARTHING)",
        dxfattribs={"layer": "E-RISER-PWR", "height": 2.9},
    ).set_placement((10.0, h_tot - 22.0))

    has_multi_boards = len(boards) > 1
    floors = [sb.floor for sb in b.sub_boards] if b.sub_boards else ["GF"]
    levels = (
        ["SITE / STREET", f"MAIN ({b.db_anchor.id})", *floors, "ROOF"]
        if has_multi_boards
        else ["SITE / STREET", *floors, "ROOF"]
    )
    n_lvls = len(levels)
    y_bot, y_top = 24.0, h_tot - 36.0
    step_y = (y_top - y_bot) / max(n_lvls - 1, 1)

    pwr_x = 58.0
    elv_x = 128.0
    earth_x = 180.0

    # Vertical spines (ELV terminates cleanly at top habitable floor, not dangling toward roof)
    y_top_floor = y_bot + max(len(levels) - 2, 1) * step_y
    msp.add_line((pwr_x, y_bot + 6), (pwr_x, y_top - 4), dxfattribs={"layer": "E-RISER-PWR"})
    msp.add_line((elv_x, y_bot + 6), (elv_x, y_top_floor + 5), dxfattribs={"layer": "E-RISER-ELV"})
    msp.add_line(
        (earth_x, y_bot + 6), (earth_x, y_top - 4), dxfattribs={"layer": "E-RISER-EARTHING"}
    )

    sm_by_floor = {sm.floor: sm for sm in design.sub_mains}
    aux_boards = [
        bd
        for bd in boards
        if bd.board_role in ("secondary_pool", "secondary_eps", "secondary_functional")
    ]
    tier_short = eac.recommended_supply_tier.split("(")[0].strip()
    first_hab_idx = 2 if has_multi_boards else 1
    for idx, lvl in enumerate(levels):
        ly = y_bot + idx * step_y
        msp.add_line((10.0, ly), (204.0, ly), dxfattribs={"layer": "E-DIN-SPARE"})
        msp.add_text(lvl, dxfattribs={"layer": "E-TITLE", "height": 2.4}).set_placement(
            (10.0, ly + 2.0)
        )

        if lvl == "SITE / STREET":
            msp.add_text(
                f"EAC METER ({tier_short})",
                dxfattribs={"layer": "E-RISER-PWR", "height": 2.1},
            ).set_placement((pwr_x - 18.0, ly + 4.0))
            msp.add_text(
                "CYTA FIBER PIT", dxfattribs={"layer": "E-RISER-ELV", "height": 2.1}
            ).set_placement((elv_x - 16.0, ly + 4.0))
            msp.add_text(
                "EARTH ROD/RING", dxfattribs={"layer": "E-RISER-EARTHING", "height": 2.0}
            ).set_placement((earth_x - 18.0, ly + 4.0))
        elif lvl.startswith("MAIN ("):
            mdb_lbl = f"{b.db_anchor.id} ({eac.recommended_main_breaker_a}A MAIN)"
            msp.add_lwpolyline(
                [
                    (pwr_x - 24, ly + 2),
                    (pwr_x + 30, ly + 2),
                    (pwr_x + 30, ly + 11),
                    (pwr_x - 24, ly + 11),
                ],
                close=True,
                dxfattribs={"layer": "E-RISER-PWR"},
            )
            _dxf_box_text(msp, mdb_lbl, pwr_x - 22.0, ly + 6.5, 50.0, 2.0, "E-RISER-PWR")
            if aux_boards:
                aux_lbl = "AUX: " + ", ".join(bd.board_id for bd in aux_boards)
                _dxf_box_text(msp, aux_lbl, elv_x - 18.0, ly + 6.5, 50.0, 1.8, "E-RISER-PV")
            msp.add_text(
                "MET / 16mm2 MEC", dxfattribs={"layer": "E-RISER-EARTHING", "height": 1.9}
            ).set_placement((earth_x - 16.0, ly + 5.0))
        elif lvl == "ROOF":
            pv_txt = (
                f"PV {b.pv_capacity_kva:g}kVA + DC/AC ISOL"
                if b.pv_capacity_kva > 0
                else "SPARE PV CONDUIT + SOLAR WH"
            )
            msp.add_text(pv_txt, dxfattribs={"layer": "E-RISER-PV", "height": 2.1}).set_placement(
                (pwr_x - 18.0, ly + 3.0)
            )
            if design.lightning_risk and design.lightning_risk.lps_required:
                msp.add_text(
                    "LPS CLASS IV AIR TERM", dxfattribs={"layer": "E-RISER-EARTHING", "height": 2.0}
                ).set_placement((earth_x - 24.0, ly + 3.0))
        else:
            sm = sm_by_floor.get(lvl)
            if sm:
                lbl = f"{sm.board_id} ({sm.cable_mm2:g}mm2, {sm.breaker_in_a}A)"
            elif has_multi_boards and aux_boards:
                lbl = ", ".join(bd.board_id for bd in aux_boards)
            else:
                lbl = f"{b.db_anchor.id} ({eac.recommended_main_breaker_a}A)"
            msp.add_lwpolyline(
                [
                    (pwr_x - 22, ly + 2),
                    (pwr_x + 28, ly + 2),
                    (pwr_x + 28, ly + 11),
                    (pwr_x - 22, ly + 11),
                ],
                close=True,
                dxfattribs={"layer": "E-RISER-PWR"},
            )
            _dxf_box_text(msp, lbl, pwr_x - 20.0, ly + 6.5, 46.0, 2.0, "E-RISER-PWR")
            elv_lbl = "9U RACK + ALARM + NVR" if idx == first_hab_idx else f"WIFI AP + DATA ({lvl})"
            msp.add_text(elv_lbl, dxfattribs={"layer": "E-RISER-ELV", "height": 2.0}).set_placement(
                (elv_x - 16.0, ly + 5.0)
            )
            msp.add_text(
                "MET / 10mm2 BOND" if idx == first_hab_idx else "PE RISER",
                dxfattribs={"layer": "E-RISER-EARTHING", "height": 1.9},
            ).set_placement((earth_x - 14.0, ly + 5.0))

    # Right Pane (x=214..414): Physical 18mm DIN-Rail DB Layouts & Selectivity Cascade
    msp.add_text(
        "B. PHYSICAL 18MM DIN-RAIL DB SCHEDULE & SELECTIVITY CASCADE",
        dxfattribs={"layer": "E-DIN-RAIL", "height": 2.9},
    ).set_placement((214.0, h_tot - 22.0))

    y_cur = h_tot - 32.0
    for brd, (head_l, encl_l, sel_l, rows_l) in zip(boards, dxf_cards, strict=True):
        for ln in head_l:
            msp.add_text(ln, dxfattribs={"layer": "E-TITLE", "height": 2.4}).set_placement(
                (214.0, y_cur)
            )
            y_cur -= 4.0
        y_cur -= 1.8
        for ln in encl_l:
            msp.add_text(ln, dxfattribs={"layer": "E-DIN-SPARE", "height": 1.9}).set_placement(
                (214.0, y_cur)
            )
            y_cur -= 3.2
        y_cur -= 2.0
        sel_layer = "E-RISER-EARTHING" if brd.selectivity_pass else "E-RISER-PWR"
        for ln in sel_l:
            msp.add_text(ln, dxfattribs={"layer": sel_layer, "height": 1.9}).set_placement(
                (214.0, y_cur)
            )
            y_cur -= 3.2
        y_cur -= 3.3

        for row, rl in zip(brd.rows, rows_l, strict=True):
            # Full row schedule text, then the scaled DIN rail bar beneath it (no overlap)
            rx0, rx1 = 216.0, 404.0
            for ln in rl:
                msp.add_text(ln, dxfattribs={"layer": "E-TITLE", "height": 1.7}).set_placement(
                    (rx0 + 2.0, y_cur)
                )
                y_cur -= 2.6
            bar_top, bar_bot = y_cur + 0.1, y_cur - 3.4
            msp.add_lwpolyline(
                [(rx0, bar_bot), (rx1, bar_bot), (rx1, bar_top), (rx0, bar_top)],
                close=True,
                dxfattribs={"layer": "E-DIN-RAIL"},
            )
            used_w = (rx1 - rx0) * (row.modules_used / max(float(row.modules_capacity), 1.0))
            if used_w > 1.0:
                msp.add_line(
                    (rx0 + used_w, bar_bot),
                    (rx0 + used_w, bar_top),
                    dxfattribs={"layer": "E-RISER-PWR"},
                )
            y_cur = bar_bot - 3.6
        y_cur -= 4.5

    psp = doc.layouts.new("E202-RISER-DB-A1")
    psp.page_setup(size=(841.0, 594.0), margins=(10.0, 10.0, 10.0, 10.0), units="mm")
    psp.add_viewport(
        center=(420.5, 297.0),
        size=(815.0, 568.0),
        view_center_point=(w_tot * 0.5, (h_tot + title_extra) * 0.5),
        view_height=max(h_tot + title_extra + 8.0, (w_tot + 8.0) * 568.0 / 815.0),
        dxfattribs={"layer": "E-TITLE"},
    )
    return doc


def _box_lines(
    text: str, box_w: float, font_px: float, *, min_font_px: float, bold: bool = True
) -> tuple[list[str], float]:
    """Fit a riser box label into <= 2 lines of `box_w` (font stepped down, never truncated)."""
    return fit_text_lines(
        text, box_w, font_px, max_lines=2, min_font_px=min_font_px, bold=bold
    )


def _svg_box_text(
    cx: float, mid_y: float, text: str, box_w: float, cls: str, style: str = ""
) -> list[str]:
    """Centred, fully-visible label lines for a riser box (vertical centre `mid_y`)."""
    base_fs = 8.3 if cls == "wlbl" else 8.6
    lines, fs = _box_lines(text, box_w - 8.0, base_fs, min_font_px=6.4)
    lh = fs * 1.12
    y0 = mid_y + fs * 0.36 - (len(lines) - 1) * lh / 2
    st = f"font-size:{fs:.1f}px;{style}" if fs != base_fs else style
    st_attr = f' style="{st}"' if st else ""
    return [
        f'  <text x="{cx:.1f}" y="{y0 + i * lh:.1f}" text-anchor="middle" class="{cls}"'
        f"{st_attr}>{esc(ln)}</text>"
        for i, ln in enumerate(lines)
    ]


def _row_label(row_no: int, used: int, cap: int, items: list[str]) -> str:
    """Full DIN-rail row label listing EVERY circuit item (wrapped by the caller)."""
    return f"Row {row_no} ({used}/{cap}M): " + ", ".join(items)


_RAIL_W = 556.0
_CARD_TXT_W = 556.0


_CardText = tuple[list[str], list[str], list[str], list[list[str]]]


def _card_layout(brd: DinRailBoardLayout) -> _CardText:
    parent_tag = f" <- {brd.parent_board_id}" if brd.parent_board_id else " [MAIN]"
    head = (
        f"{brd.board_id} ({brd.floor}){parent_tag} — {brd.total_modules_used}M / "
        f"{brd.total_modules_capacity}M ({brd.spare_pct:.0f}% spare)"
    )
    head_lines = wrap_text_to_width(head, _CARD_TXT_W, 11.5, bold=True)
    encl_lines = wrap_text_to_width(
        f"{brd.enclosure_spec} | {brd.enclosure_dims_mm}", _CARD_TXT_W, 8.5
    )
    sel_full = (
        f"Ipf={brd.incoming_ipf_ka:.1f} kA <= Icu={brd.min_device_icu_ka:.0f} kA | "
        f"{brd.selectivity_note}"
    )
    sel_lines = wrap_text_to_width(sel_full, _CARD_TXT_W, 8.6, bold=True)
    row_lines = [
        wrap_text_to_width(
            _row_label(r.row_no, r.modules_used, r.modules_capacity, r.items),
            _RAIL_W - 8.0,
            8.5,
        )
        for r in brd.rows
    ]
    return head_lines, encl_lines, sel_lines, row_lines


def _card_height(
    head: list[str], encl: list[str], sel: list[str], rows: list[list[str]]
) -> float:
    h = 19.0 + (len(head) - 1) * 15.0 + len(encl) * 12.0 + 4.0 + len(sel) * 14.0 + 2.0
    for rl in rows:
        h += len(rl) * 11.0 + 3.0 + 9.0 + 9.0
    return h + 6.0


def render_riser_and_db_svg(result: Phase2DesignResult | ElectricalDesignResult) -> str:
    """Render E-202 Vertical Riser Diagram & Physical 18mm DIN-Rail DB Elevation as SVG.

    Every board, every DIN row and every circuit item is listed in full (wrapped onto extra
    lines; the sheet grows) - nothing is truncated or summarised as "(+N more)".
    """
    design = _unwrap(result)
    b = design.building
    eac = design.eac_compliance
    boards = design.din_rail_boards
    has_multi_boards = len(boards) > 1
    floors = [sb.floor for sb in b.sub_boards] if b.sub_boards else ["GF"]
    levels = (
        ["SITE / STREET", f"MAIN ({b.db_anchor.id})", *floors, "ROOF"]
        if has_multi_boards
        else ["SITE / STREET", *floors, "ROOF"]
    )
    sm_by_floor = {sm.floor: sm for sm in design.sub_mains}
    aux_boards = [
        bd
        for bd in boards
        if bd.board_role in ("secondary_pool", "secondary_eps", "secondary_functional")
    ]
    tier_short = eac.recommended_supply_tier.split("(")[0].strip()

    svg_w = 1190
    # Header (wrapped, never clipped by the sheet edge)
    title_lines = wrap_text_to_width(
        f"E-202 VERTICAL RISER & 18mm DIN-RAIL DB SCHEDULE — {b.project_name}",
        svg_w - 52.0,
        15.0,
        bold=True,
    )
    sub_lines = wrap_text_to_width(
        f"Supply: {eac.recommended_supply_tier} | "
        f"Earthing: {design.earthing_system.value} | "
        f"Ipf: {design.prospective_fault_current_ka:.1f} kA | Boards: {len(boards)} "
        "(See E-204 for Full Module Elevations & Door Schedules)",
        svg_w - 52.0,
        10.0,
    )
    hdr_extra = (len(title_lines) - 1) * 18.0 + (len(sub_lines) - 1) * 13.0

    # Pre-compute exact right-pane height so cards never clip at the bottom
    cards = [(brd, *_card_layout(brd)) for brd in boards]
    total_boards_h = 108.0 + sum(_card_height(*c[1:]) + 16.0 for c in cards) + 20.0
    body_h = int(max(760, total_boards_h + 30.0))
    svg_h = int(body_h + hdr_extra)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="cy-e202" '
            f'viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}">'
        ),
        "  <style>",
        '    .cy-e202 text { font-family: "DejaVu Sans", Arial, sans-serif; fill: #1a202c; }',
        "    .cy-e202 .hdr { font-size: 15px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e202 .sub { font-size: 10px; fill: #4a5568; }",
        "    .cy-e202 .sec { font-size: 11.5px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e202 .lbl { font-size: 8.6px; font-weight: 700; }",
        "    .cy-e202 .wlbl { font-size: 8.3px; font-weight: 700; fill: #ffffff; }",
        "    .cy-e202 .txt { font-size: 8.5px; fill: #2d3748; }",
        "  </style>",
        f'  <rect x="0" y="0" width="{svg_w}" height="{svg_h}" fill="#ffffff" />',
        (
            f'  <rect x="12" y="12" width="{svg_w - 24}" height="{svg_h - 24}" '
            'fill="none" stroke="#1b365d" stroke-width="2" />'
        ),
    ]
    for i, t_line in enumerate(title_lines):
        parts.append(f'  <text x="26" y="{38 + i * 18}" class="hdr">{esc(t_line)}</text>')
    sub_y0 = 55 + (len(title_lines) - 1) * 18
    for i, s_line in enumerate(sub_lines):
        parts.append(f'  <text x="26" y="{sub_y0 + i * 13}" class="sub">{esc(s_line)}</text>')
    if hdr_extra:
        parts.append(f'  <g transform="translate(0,{hdr_extra:.0f})">')
    parts.extend(
        [
            (
                f'  <line x1="12" y1="66" x2="{svg_w - 12}" y2="66" '
                'stroke="#cbd5e0" stroke-width="1.5" />'
            ),
            (
                '  <text x="28" y="90" class="sec">'
                "A. VERTICAL RISER SCHEMATIC (POWER / ELV / PV / EARTHING)</text>"
            ),
            (
                f'  <line x1="560" y1="66" x2="560" y2="{body_h - 16}" '
                'stroke="#cbd5e0" stroke-width="1.5" />'
            ),
        ]
    )

    y_top, y_bot = 135.0, min(680.0, body_h - 60.0)
    step_y = (y_bot - y_top) / max(len(levels) - 1, 1)
    y_elv_top = y_bot - max(len(levels) - 2, 1) * step_y - 14.0
    pwr_x, elv_x, ear_x = 180.0, 356.0, 496.0
    first_hab_idx = 2 if has_multi_boards else 1

    # Vertical riser bus lines (ELV stops cleanly at highest habitable floor, not dangling)
    parts.append(
        f'  <line x1="{pwr_x}" y1="{y_top}" x2="{pwr_x}" y2="{y_bot}" '
        'stroke="#c53030" stroke-width="3.5" />'
    )
    parts.append(
        f'  <line x1="{elv_x}" y1="{y_elv_top:.1f}" x2="{elv_x}" y2="{y_bot}" '
        'stroke="#2b6cb0" stroke-width="2.5" stroke-dasharray="6,3" />'
    )
    parts.append(
        f'  <line x1="{ear_x}" y1="{y_top}" x2="{ear_x}" y2="{y_bot}" '
        'stroke="#2f855a" stroke-width="2.5" />'
    )

    def box(
        cx: float, ly: float, w: float, h: float, fill: str, stroke: str, sw: float
    ) -> str:
        stroke_attr = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        return (
            f'  <rect x="{cx - w / 2:g}" y="{ly - h - 2:g}" width="{w:g}" height="{h:g}" '
            f'rx="4" fill="{fill}"{stroke_attr} />'
        )

    for idx, lvl in enumerate(levels):
        # Bottom-up rendering (SITE at bottom, ROOF at top)
        ly = y_bot - idx * step_y
        mid = ly - 14.0
        parts.append(
            f'  <line x1="26" y1="{ly:.1f}" x2="548" y2="{ly:.1f}" '
            'stroke="#e2e8f0" stroke-width="1.5" stroke-dasharray="4,4" />'
        )
        lvl_lines, lvl_fs = _box_lines(lvl, 76.0, 8.6, min_font_px=6.4)
        for li, l_txt in enumerate(lvl_lines):
            fs_css = f"font-size:{lvl_fs:.1f}px;" if lvl_fs != 8.6 else ""
            parts.append(
                f'  <text x="28" y="{ly - 6 - (len(lvl_lines) - 1 - li) * lvl_fs * 1.12:.1f}" '
                f'class="lbl" style="{fs_css}fill:#4a5568;">{esc(l_txt)}</text>'
            )
        if lvl == "SITE / STREET":
            parts.append(box(pwr_x, ly, 136, 24, "#c53030", "", 0))
            parts += _svg_box_text(pwr_x, mid, f"EAC METER ({tier_short})", 136, "wlbl")
            parts.append(box(elv_x, ly, 100, 24, "#2b6cb0", "", 0))
            parts += _svg_box_text(elv_x, mid, "CYTA FIBER PIT", 100, "wlbl")
            parts.append(box(ear_x, ly, 96, 24, "#2f855a", "", 0))
            parts += _svg_box_text(ear_x, mid, "EARTH ELECTRODE", 96, "wlbl")
        elif lvl.startswith("MAIN ("):
            mdb_lbl = f"{b.db_anchor.id} ({eac.recommended_main_breaker_a}A MAIN)"
            parts.append(box(pwr_x, ly, 144, 24, "#1b365d", "#0f172a", 1.6))
            parts += _svg_box_text(pwr_x, mid, mdb_lbl, 144, "wlbl")
            if aux_boards:
                aux_lbl = " / ".join(bd.board_id for bd in aux_boards)
                parts.append(
                    f'  <line x1="{pwr_x + 72}" y1="{ly - 14}" x2="{elv_x - 66}" y2="{ly - 14}" '
                    'stroke="#0f766e" stroke-width="2" stroke-dasharray="4,2" />'
                )
                parts.append(box(elv_x, ly, 132, 24, "#f0fdfa", "#0f766e", 1.4))
                parts += _svg_box_text(elv_x, mid, aux_lbl, 132, "lbl", "fill:#0f766e;")
            parts.append(box(ear_x, ly, 96, 24, "#f0fff4", "#2f855a", 1.4))
            parts += _svg_box_text(ear_x, mid, "MET / 16mm² MEC", 96, "lbl", "fill:#22543d;")
        elif lvl == "ROOF":
            pv_s = (
                f"PV {b.pv_capacity_kva:g}kVA + ISOL"
                if b.pv_capacity_kva > 0
                else "PV CONDUIT + SOLAR WH"
            )
            parts.append(box(pwr_x, ly, 136, 22, "#d69e2e", "", 0))
            parts += _svg_box_text(pwr_x, ly - 13.0, pv_s, 136, "wlbl")
            lps_s = (
                "LPS CLASS IV"
                if (design.lightning_risk and design.lightning_risk.lps_required)
                else "ROOF BONDING"
            )
            parts.append(box(ear_x, ly, 96, 22, "#2f855a", "", 0))
            parts += _svg_box_text(ear_x, ly - 13.0, lps_s, 96, "wlbl")
        else:
            sm = sm_by_floor.get(lvl)
            if sm:
                p_lbl = f"{sm.board_id} ({sm.cable_mm2:g}mm² {sm.breaker_in_a}A)"
            elif has_multi_boards and aux_boards:
                p_lbl = " + ".join(bd.board_id for bd in aux_boards)
            else:
                p_lbl = f"{b.db_anchor.id} ({eac.recommended_main_breaker_a}A)"
            parts.append(box(pwr_x, ly, 144, 24, "#fff5f5", "#c53030", 1.6))
            parts += _svg_box_text(pwr_x, mid, p_lbl, 144, "lbl", "fill:#9b2c2c;")
            e_lbl = "9U RACK + NVR + ALARM" if idx == first_hab_idx else f"WIFI AP + DATA ({lvl})"
            parts.append(box(elv_x, ly, 136, 24, "#ebf8ff", "#2b6cb0", 1.4))
            parts += _svg_box_text(elv_x, mid, e_lbl, 136, "lbl", "fill:#2c5282;")
            g_lbl = "MET / 10mm² BOND" if idx == first_hab_idx else "PE RISER 10mm²"
            parts.append(box(ear_x, ly, 96, 24, "#f0fff4", "#2f855a", 1.4))
            parts += _svg_box_text(ear_x, mid, g_lbl, 96, "lbl", "fill:#22543d;")

    # Right Pane: Physical 18mm DIN-Rail DB Summary (full scaled elevations on Sheet E-204)
    parts.append(
        '  <text x="580" y="90" class="sec">'
        "B. MAIN &amp; SECONDARY DB SUMMARY (FULL ELEVATIONS ON SHEET E-204)</text>"
    )
    y_b = 108.0
    for brd, head_lines, encl_lines, sel_lines, row_lines in cards:
        box_h = _card_height(head_lines, encl_lines, sel_lines, row_lines)
        parts.append(
            f'  <rect x="580" y="{y_b:.1f}" width="584" height="{box_h:.1f}" rx="6" '
            'fill="#f8fafc" stroke="#cbd5e0" stroke-width="1.5" />'
        )
        hy = y_b + 19.0
        for h_line in head_lines:
            parts.append(f'  <text x="594" y="{hy:.1f}" class="sec">{esc(h_line)}</text>')
            hy += 15.0
        ey = hy + 1.0
        for e_line in encl_lines:
            parts.append(f'  <text x="594" y="{ey:.1f}" class="txt">{esc(e_line)}</text>')
            ey += 12.0
        sel_col = "#2f855a" if brd.selectivity_pass else "#c53030"
        sy = ey + 3.0
        for sline in sel_lines:
            parts.append(
                f'  <text x="594" y="{sy:.1f}" class="lbl" style="fill:{sel_col};">'
                f"{esc(sline)}</text>"
            )
            sy += 14.0
        ry = sy - 2.0
        for row, rl in zip(brd.rows, row_lines, strict=True):
            # Row schedule text first, then the scaled rail bar BELOW it - no bar edge or
            # used-capacity marker can ever strike through the circuit list.
            ty = ry + 10.0
            for r_line in rl:
                parts.append(f'  <text x="602" y="{ty:.1f}" class="txt">{esc(r_line)}</text>')
                ty += 11.0
            bar_y = ry + len(rl) * 11.0 + 3.0
            used_w = _RAIL_W * (row.modules_used / max(float(row.modules_capacity), 1.0))
            parts.append(
                f'  <rect x="594" y="{bar_y:.1f}" width="{_RAIL_W:.1f}" height="9" rx="2" '
                'fill="#edf2f7" stroke="#a0aec0" stroke-width="1" />'
            )
            if used_w > 1.0:
                parts.append(
                    f'  <rect x="594" y="{bar_y:.1f}" width="{used_w:.1f}" height="9" rx="2" '
                    'fill="#bee3f8" stroke="#2b6cb0" stroke-width="1" />'
                )
            ry = bar_y + 9.0 + 9.0
        y_b += box_h + 16.0

    if hdr_extra:
        parts.append("  </g>")
    parts.append("</svg>")
    return "\n".join(parts)


def export_riser_and_db_deliverables(
    result: Phase2DesignResult | ElectricalDesignResult,
    out_dir: Path,
) -> dict[str, Path]:
    """Write E-202 Vertical Riser & Physical 18mm DIN-Rail DB DXF + SVG."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_p = out_dir / "E202_riser_and_db_layout.dxf"
    svg_p = out_dir / "E202_riser_and_db_layout.svg"
    render_riser_and_db_dxf(result).saveas(str(dxf_p))
    svg_p.write_text(render_riser_and_db_svg(result), encoding="utf-8")
    return {"e202_riser_dxf": dxf_p, "e202_riser_svg": svg_p}
