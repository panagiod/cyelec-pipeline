"""E-204 Main & Secondary Distribution Board Designs — Elevations, IEC 61439 Specs & Door Schedules.

Generates buildable panel-assembly drawings (E204_board_designs.svg, .dxf & .pdf) for every
Main Board (MDB / DB1) and Secondary Board (DB-GF, DB-1F, DB-POOL IP65, DB-EPS Backup,
SDB-LP, SDB-HVAC), including true-scale 18 mm DIN-rail / MCCB module front elevations,
IEC 60446 phase striping, IEC 60890 thermal dissipation checks, and printable BS 7671
Reg 514.9.1 Inside-Door Circuit Directories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape as esc
from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import (
    BoardModuleSlot,
    DinRailBoardLayout,
    ElectricalDesignResult,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.pdf_export import export_svg_sheet_to_pdf
from cyelec.render.svg_utils import unwrap_design as _unwrap
from cyelec.render.text_fit import (
    fit_single_line_font,
    fit_text_lines,
    text_width_px,
    wrap_text_chars,
)

_ROLE_META: dict[str, tuple[str, str]] = {
    "main": ("MAIN BOARD", "#1b365d"),
    "secondary_floor": ("SECONDARY FLOOR DB", "#3730a3"),
    "secondary_pool": ("SECONDARY IP65 POOL DB", "#0f766e"),
    "secondary_eps": ("SECONDARY EPS BACKUP DB", "#b45309"),
    "secondary_functional": ("SECONDARY FUNCTIONAL SDB", "#1e40af"),
}

_SLOT_STYLE: dict[str, tuple[str, str]] = {
    "incomer": ("#fee2e2", "#dc2626"),
    "rcd_s": ("#fef3c7", "#d97706"),
    "ats": ("#fef3c7", "#b45309"),
    "spd": ("#ffedd5", "#ea580c"),
    "pv_isol": ("#fef9c3", "#ca8a04"),
    "feeder": ("#ede9fe", "#7c3aed"),
    "meter": ("#e0f2fe", "#0284c7"),
    "knx": ("#ccfbf1", "#0d9488"),
    "rcbo": ("#ffffff", "#334155"),
    "spare": ("#f1f5f9", "#94a3b8"),
}

_PHASE_COLOR: dict[str, str] = {
    "L1": "#92400e",
    "L2": "#1e293b",
    "L3": "#64748b",
    "3P": "#1d4ed8",
    "N/A": "#cbd5e1",
}


def _slot_text(text: str, slot_px: float, font_px: float, *, bold: bool) -> tuple[str, float]:
    """Full module-face text with font fitted to the slot (never character-truncated)."""
    fs = fit_single_line_font(text, slot_px - 2.0, font_px, min_font_px=4.2, bold=bold)
    return text, fs


def _slot_lines(sl: BoardModuleSlot) -> tuple[str, str]:
    """Module-face (top_code, bottom_rating) - full model strings, no abbreviation cuts."""
    if sl.kind == "spare":
        return "SPARE", sl.label or f"{sl.width_m}M"
    return sl.code, sl.label


def _wrapped(
    text: str, max_w: float, font_px: float, *, bold: bool = False, min_font_px: float | None = None
) -> tuple[list[str], float]:
    return fit_text_lines(
        text,
        max_w,
        font_px,
        max_lines=1,
        min_font_px=min_font_px if min_font_px is not None else font_px,
        bold=bold,
    )


@dataclass
class _CardLayout:
    """Pre-computed, collision-free E-204 board card layout (all text full-length)."""

    role_col: str
    r1: str
    r2: str
    h1: list[str]
    h1_fs: float
    h2: list[str]
    h2_fs: float
    h2_y0: float
    header_h: float
    cab_h: float
    thermal: str
    load_txt: str
    load_same_row: bool
    bars: list[str]
    bars_fs: float
    sel: list[str]
    sel_fs: float
    rows_cells: list[tuple[list[tuple[list[str], float]], float]] = field(default_factory=list)
    h: float = 0.0


_DOOR_COLS: tuple[tuple[float, float], ...] = (
    (6.0, 24.0),  # Way
    (34.0, 40.0),  # ID
    (78.0, 24.0),  # Ph
    (106.0, 132.0),  # Protective Device
    (244.0, 94.0),  # Cable
    (344.0, 236.0),  # Load / Room
)


def _compute_board_card_layout(
    brd: DinRailBoardLayout,
    *,
    svg_w: int,
    left_w: float,
) -> _CardLayout:
    """Pre-compute collision-free E-204 board card layout with full unwrapped text."""
    role_lbl, role_col = _ROLE_META.get(brd.board_role, ("BOARD", "#1b365d"))
    parent_txt = (
        f"Fed from {brd.parent_board_id}" if brd.parent_board_id else "EAC Service Origin"
    )
    r1 = f"{brd.total_modules_used}/{brd.total_modules_capacity}M ({brd.spare_pct:.0f}% spare)"
    r2 = f"Dims: {brd.enclosure_dims_mm} | {brd.ip_rating} | Busbar: {brd.busbar_rating_a}A Cu"
    av1 = (svg_w - 30) - text_width_px(r1, 11.0, bold=True) - 16.0 - 30.0
    av2 = (svg_w - 30) - text_width_px(r2, 8.5) - 16.0 - 30.0
    h1, h1_fs = _wrapped(
        f"{brd.board_id} ({brd.floor}) — {role_lbl} | {brd.standard_ref}",
        av1,
        11.0,
        bold=True,
        min_font_px=9.5,
    )
    h2, h2_fs = _wrapped(
        f"{parent_txt} · Supply: {brd.incoming_supply_spec}",
        av2,
        8.5,
        min_font_px=7.6,
    )
    h1_lh, h2_lh = 13.0, 10.5
    h2_y0 = 15.0 + (len(h1) - 1) * h1_lh + 13.0
    header_h = max(34.0, h2_y0 + (len(h2) - 1) * h2_lh + 8.0)

    n_rows = max(1, len(brd.rows))
    cab_h = 26.0 + n_rows * 62.0 + 18.0
    th_txt = "PASS" if brd.thermal_pass else "UPGRADE ENCLOSURE"
    thermal = (
        f"IEC 60890 Thermal (40°C): P_loss = {brd.power_dissipation_w:.1f} W <= "
        f"P_max = {brd.max_enclosure_dissipation_w:.0f} W [{th_txt}]"
    )
    pl = brd.phase_loads_a
    ph_str = (
        f"L1: {pl.get('L1', 0.0):.1f}A | L2: {pl.get('L2', 0.0):.1f}A | "
        f"L3: {pl.get('L3', 0.0):.1f}A"
        if pl
        else "Feeder Bus"
    )
    load_txt = f"Board Load: {ph_str}"
    load_same_row = (
        text_width_px(thermal, 8.0, bold=True) + text_width_px(load_txt, 7.8) + 16.0
        <= left_w
    )
    bars, bars_fs = _wrapped(f"Bars: {brd.terminal_bars_spec}", left_w, 7.8, min_font_px=7.2)
    sel, sel_fs = _wrapped(
        f"Selectivity: {brd.selectivity_note}", left_w, 7.8, min_font_px=7.2
    )
    foot_lh = 10.5
    n_foot = 1 + (0 if load_same_row else 1) + len(bars) + len(sel)
    elev_h = header_h + 8.0 + 16.0 + cab_h + 13.0 + (n_foot - 1) * foot_lh + 4.0 + 10.0

    rows_cells: list[tuple[list[tuple[list[str], float]], float]] = []
    for lr in brd.door_legend:
        vals = (
            f"R{lr.rail_no}.{lr.way_no}",
            lr.circuit_id,
            lr.phase,
            lr.device_spec,
            lr.cable_spec,
            lr.description,
        )
        cells = [
            fit_text_lines(v, cw, 7.4, max_lines=2, min_font_px=6.6)
            for v, (_ox, cw) in zip(vals, _DOOR_COLS, strict=True)
        ]
        n_ln = max(len(c[0]) for c in cells)
        rows_cells.append((cells, 16.5 + (n_ln - 1) * 9.0))
    tbl_h = header_h + 8.0 + 16.0 + 18.0 + sum(rh for _c, rh in rows_cells) + 22.0
    if not rows_cells:
        tbl_h += 16.5
    return _CardLayout(
        role_col=role_col,
        r1=r1,
        r2=r2,
        h1=h1,
        h1_fs=h1_fs,
        h2=h2,
        h2_fs=h2_fs,
        h2_y0=h2_y0,
        header_h=header_h,
        cab_h=cab_h,
        thermal=thermal,
        load_txt=load_txt,
        load_same_row=load_same_row,
        bars=bars,
        bars_fs=bars_fs,
        sel=sel,
        sel_fs=sel_fs,
        rows_cells=rows_cells,
        h=max(elev_h, tbl_h),
    )


def _render_board_elevation_svg(
    parts: list[str],
    brd: DinRailBoardLayout,
    cd: _CardLayout,
    *,
    y_card: float,
    left_x: float,
    left_w: float,
) -> None:
    """Append left-column physical 18 mm DIN-rail elevation and thermal/terminal footer."""
    ex = left_x
    ey = y_card + cd.header_h + 8.0
    ew = left_w
    cab_h = cd.cab_h

    parts.append(
        f'  <text x="{ex:.1f}" y="{ey + 10:.1f}" class="sec">'
        "PHYSICAL FRONT ELEVATION (TRUE 18mm MODULE SCALE)</text>"
    )
    parts.append(
        f'  <text x="{ex + ew:.1f}" y="{ey + 10:.1f}" text-anchor="end" class="xs">'
        f"Ipf {brd.incoming_ipf_ka:.2f} kA &lt;= Icu {brd.min_device_icu_ka:.0f} kA</text>"
    )

    cy0 = ey + 16.0
    cab_fill = "#f0fdfa" if brd.board_role == "secondary_pool" else "#f8fafc"
    cab_stroke = "#0f766e" if brd.board_role == "secondary_pool" else "#475569"
    parts.append(
        f'  <rect x="{ex:.1f}" y="{cy0:.1f}" width="{ew:.1f}" height="{cab_h:.1f}" rx="4" '
        f'fill="{cab_fill}" stroke="{cab_stroke}" stroke-width="1.8" />'
    )
    bar_y = cy0 + 6.0
    parts.append(
        f'  <rect x="{ex + 12:.1f}" y="{bar_y:.1f}" width="250" height="12" rx="2" '
        'fill="#dbeafe" stroke="#2563eb" stroke-width="1" />'
    )
    parts.append(
        f'  <text x="{ex + 137:.1f}" y="{bar_y + 9:.1f}" text-anchor="middle" '
        'style="font-size:7px;font-weight:700;fill:#1e40af;">NEUTRAL TERMINAL BAR (N)</text>'
    )
    pe_lbl = (
        "PE + SEC.702 BONDING BAR"
        if brd.board_role == "secondary_pool"
        else "EARTH / PROTECTIVE BAR (PE)"
    )
    parts.append(
        f'  <rect x="{ex + ew - 262:.1f}" y="{bar_y:.1f}" width="250" height="12" rx="2" '
        'fill="#dcfce7" stroke="#16a34a" stroke-width="1" />'
    )
    parts.append(
        f'  <text x="{ex + ew - 137:.1f}" y="{bar_y + 9:.1f}" text-anchor="middle" '
        f'style="font-size:7px;font-weight:700;fill:#166534;">{pe_lbl}</text>'
    )

    rail_x0 = ex + 26.0
    rail_w = ew - 38.0
    ry = cy0 + 26.0
    for row in brd.rows:
        cap_m = max(12, row.modules_capacity)
        parts.append(
            f'  <text x="{ex + 12:.1f}" y="{ry + 28:.1f}" text-anchor="middle" '
            f'class="th">R{row.row_no}</text>'
        )
        parts.append(
            f'  <rect x="{rail_x0 - 3:.1f}" y="{ry + 17:.1f}" width="{rail_w + 6:.1f}" '
            'height="16" fill="#cbd5e1" stroke="#94a3b8" stroke-width="0.7" />'
        )
        px_per_m = rail_w / float(cap_m)
        for sl in row.slots:
            sx = rail_x0 + (sl.slot_start_m - 1) * px_per_m + 0.8
            sw = max(6.0, sl.width_m * px_per_m - 1.6)
            s_fill, s_stroke = _SLOT_STYLE.get(sl.kind, ("#ffffff", "#334155"))
            dash = ' stroke-dasharray="3,2"' if sl.kind == "spare" else ""
            parts.append(
                f'  <rect x="{sx:.1f}" y="{ry:.1f}" width="{sw:.1f}" height="48" rx="2" '
                f'fill="{s_fill}" stroke="{s_stroke}" stroke-width="1.1"{dash} />'
            )
            if sl.kind != "spare":
                p_col = _PHASE_COLOR.get(sl.phase, "#94a3b8")
                parts.append(
                    f'  <rect x="{sx + 0.6:.1f}" y="{ry + 0.6:.1f}" '
                    f'width="{max(2.0, sw - 1.2):.1f}" height="4.5" fill="{p_col}" />'
                )
                if sl.kind not in ("meter", "knx"):
                    tw = min(14.0, max(5.0, sw * 0.42))
                    tx = sx + (sw - tw) / 2.0
                    parts.append(
                        f'  <rect x="{tx:.1f}" y="{ry + 20.5:.1f}" width="{tw:.1f}" '
                        'height="6.5" rx="1" fill="#1e293b" />'
                    )
            top_t, bot_t = _slot_lines(sl)
            cx_m = sx + sw / 2.0
            if top_t:
                t_txt, t_fs = _slot_text(top_t, sw, 6.8, bold=True)
                parts.append(
                    f'  <text x="{cx_m:.1f}" y="{ry + 15.5:.1f}" text-anchor="middle" '
                    f'class="mcode" style="font-size:{t_fs}px;">{esc(t_txt)}</text>'
                )
            if bot_t:
                b_txt, b_fs = _slot_text(bot_t, sw, 6.1, bold=False)
                parts.append(
                    f'  <text x="{cx_m:.1f}" y="{ry + 41.5:.1f}" text-anchor="middle" '
                    f'class="mlbl" style="font-size:{b_fs}px;">{esc(b_txt)}</text>'
                )
        ry += 62.0

    gy = cy0 + cab_h - 13.0
    parts.append(
        f'  <line x1="{ex + 10:.1f}" y1="{gy:.1f}" x2="{ex + ew - 10:.1f}" y2="{gy:.1f}" '
        'stroke="#94a3b8" stroke-width="1" stroke-dasharray="4,3" />'
    )
    parts.append(
        f'  <text x="{ex + ew / 2.0:.1f}" y="{gy + 9.5:.1f}" text-anchor="middle" '
        'style="font-size:6.8px;fill:#64748b;">REMOVABLE CABLE GLAND ENTRY PLATE '
        "(BOTTOM / TOP CONDUIT &amp; SWA GLANDS)</text>"
    )

    fy = cy0 + cab_h + 13.0
    th_cls = "ok" if brd.thermal_pass else "warn"
    parts.append(
        f'  <text x="{ex:.1f}" y="{fy:.1f}" class="{th_cls}">{esc(cd.thermal)}</text>'
    )
    if cd.load_same_row:
        parts.append(
            f'  <text x="{ex + ew:.1f}" y="{fy:.1f}" text-anchor="end" class="xs">'
            f"{esc(cd.load_txt)}</text>"
        )
    else:
        fy += 10.5
        parts.append(
            f'  <text x="{ex:.1f}" y="{fy:.1f}" class="xs">{esc(cd.load_txt)}</text>'
        )
    for f_lines, f_fs in ((cd.bars, cd.bars_fs), (cd.sel, cd.sel_fs)):
        for ln in f_lines:
            fy += 10.5
            parts.append(
                f'  <text x="{ex:.1f}" y="{fy:.1f}" class="xs" '
                f'style="font-size:{f_fs}px;">{esc(ln)}</text>'
            )


def _render_board_door_directory_svg(
    parts: list[str],
    cd: _CardLayout,
    *,
    y_card: float,
    right_x: float,
    right_w: float,
) -> None:
    """Append right-column BS 7671 Reg 514.9.1 Inside-Door Circuit Directory table."""
    tx0 = right_x
    ty0 = y_card + cd.header_h + 8.0
    parts.append(
        f'  <text x="{tx0:.1f}" y="{ty0 + 10:.1f}" class="sec">'
        "BS 7671 REG 514.9.1 INSIDE-DOOR CIRCUIT DIRECTORY</text>"
    )
    th_y = ty0 + 16.0
    parts.append(
        f'  <rect x="{tx0:.1f}" y="{th_y:.1f}" width="{right_w:.1f}" height="18" '
        'fill="#e2e8f0" stroke="#cbd5e1" stroke-width="0.8" />'
    )
    for (ox, _cw), htxt in zip(
        _DOOR_COLS,
        ("Way", "ID", "Ph", "Protective Device", "Cable", "Load / Room"),
        strict=True,
    ):
        parts.append(
            f'  <text x="{tx0 + ox:.1f}" y="{th_y + 12.2:.1f}" class="th">{htxt}</text>'
        )
    row_y = th_y + 18.0
    for idx_l, (cells, rh) in enumerate(cd.rows_cells):
        bg = "#f8fafc" if idx_l % 2 == 1 else "#ffffff"
        parts.append(
            f'  <rect x="{tx0:.1f}" y="{row_y:.1f}" width="{right_w:.1f}" height="{rh:.1f}" '
            f'fill="{bg}" stroke="#e2e8f0" stroke-width="0.5" />'
        )
        for (ox, _cw), (c_lines, c_fs) in zip(_DOOR_COLS, cells, strict=True):
            for li, c_ln in enumerate(c_lines):
                parts.append(
                    f'  <text x="{tx0 + ox:.1f}" y="{row_y + 11.4 + li * 9.0:.1f}" '
                    f'class="td" style="font-size:{c_fs}px;">{esc(c_ln)}</text>'
                )
        row_y += rh


def render_board_designs_svg(result: Phase2DesignResult | ElectricalDesignResult) -> str:
    """Render Sheet E-204 Main & Secondary Distribution Board Front Elevations & Door Schedules.

    Every label is rendered in full: long project names, standard references, supply specs,
    selectivity notes and door-directory cells are word-wrapped and their cards/rows grow to
    fit (no truncation, no ellipsis, no text running outside its box).
    """
    design = _unwrap(result)
    b = design.building
    eac = design.eac_compliance
    boards = design.din_rail_boards

    svg_w = 1240
    left_x, left_w = 24.0, 590.0
    right_x, right_w = 630.0, 586.0

    main_cnt = sum(1 for bd in boards if bd.board_role == "main")
    sec_cnt = len(boards) - main_cnt
    sec_names = ", ".join(bd.board_id for bd in boards if bd.board_role != "main") or "None needed"

    title_lines, title_fs = fit_text_lines(
        f"E-204 MAIN & SECONDARY DISTRIBUTION BOARD DESIGNS — {b.project_name}",
        float(svg_w - 48),
        14.5,
        max_lines=1,
        min_font_px=11.0,
        bold=True,
    )
    sub_lines, sub_fs = fit_text_lines(
        f"Supply: {eac.recommended_supply_tier} | Earthing: {design.earthing_system.value} | "
        f"Main: {main_cnt} | Secondary: {sec_cnt} ({sec_names})",
        820.0,
        9.5,
        max_lines=1,
        min_font_px=8.0,
    )
    title_lh = round(title_fs * 1.25, 1)
    sub_lh = round(sub_fs * 1.35, 1)
    sub_y0 = 34.0 + (len(title_lines) - 1) * title_lh + 17.0
    hdr_line_y = sub_y0 + (len(sub_lines) - 1) * sub_lh + 11.0

    cards = [_compute_board_card_layout(brd, svg_w=svg_w, left_w=left_w) for brd in boards]
    y_first = hdr_line_y + 12.0
    svg_h = int(max(760.0, y_first + 22.0 + sum(c.h + 18.0 for c in cards) + 34.0))

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="cy-e204" '
            f'viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}">'
        ),
        "  <style>",
        '    .cy-e204 text { font-family: "DejaVu Sans", Arial, sans-serif; fill: #0f172a; }',
        "    .cy-e204 .hdr { font-size: 14.5px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e204 .sub { font-size: 9.5px; fill: #475569; }",
        "    .cy-e204 .whdr { font-size: 11px; font-weight: 700; fill: #ffffff; }",
        "    .cy-e204 .wsub { font-size: 8.5px; fill: #e2e8f0; }",
        "    .cy-e204 .badge { font-size: 8px; font-weight: 700; fill: #ffffff; }",
        "    .cy-e204 .sec { font-size: 9px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e204 .xs { font-size: 7.8px; fill: #334155; }",
        "    .cy-e204 .th { font-size: 7.8px; font-weight: 700; fill: #1e293b; }",
        "    .cy-e204 .td { font-size: 7.4px; fill: #1e293b; }",
        "    .cy-e204 .ok { font-size: 8px; font-weight: 700; fill: #166534; }",
        "    .cy-e204 .warn { font-size: 8px; font-weight: 700; fill: #b91c1c; }",
        "    .cy-e204 .mcode { font-size: 6.8px; font-weight: 700; fill: #0f172a; }",
        "    .cy-e204 .mlbl { font-size: 6.1px; fill: #475569; }",
        "  </style>",
        f'  <rect x="0" y="0" width="{svg_w}" height="{svg_h}" fill="#ffffff" />',
        (
            f'  <rect x="10" y="10" width="{svg_w - 20}" height="{svg_h - 20}" '
            'fill="none" stroke="#1b365d" stroke-width="2" />'
        ),
    ]
    for i, ln in enumerate(title_lines):
        parts.append(
            f'  <text x="24" y="{34.0 + i * title_lh:.1f}" class="hdr" '
            f'style="font-size:{title_fs}px;">{esc(ln)}</text>'
        )
    for i, ln in enumerate(sub_lines):
        parts.append(
            f'  <text x="24" y="{sub_y0 + i * sub_lh:.1f}" class="sub" '
            f'style="font-size:{sub_fs}px;">{esc(ln)}</text>'
        )
    ky, kt = sub_y0 - 8.0, sub_y0 - 1.0
    for kx, fill, extra, lbl in (
        (856, "#92400e", "", "L1"),
        (896, "#1e293b", "", "L2"),
        (936, "#64748b", "", "L3"),
        (976, "#1d4ed8", "", "3P"),
        (1020, "#fee2e2", ' stroke="#dc2626"', "Incomer"),
        (1088, "#ede9fe", ' stroke="#7c3aed"', "Feeder"),
        (1152, "#f1f5f9", ' stroke="#94a3b8" stroke-dasharray="2,2"', "Spare"),
    ):
        parts.append(
            f'  <rect x="{kx}" y="{ky:.1f}" width="12" height="8" fill="{fill}"{extra} />'
        )
        parts.append(f'  <text x="{kx + 16}" y="{kt:.1f}" class="xs">{lbl}</text>')
    parts.append(
        f'  <line x1="10" y1="{hdr_line_y:.1f}" x2="{svg_w - 10}" y2="{hdr_line_y:.1f}" '
        'stroke="#cbd5e1" stroke-width="1.4" />'
    )

    y_card = y_first
    for brd, cd in zip(boards, cards, strict=True):
        parts.append(
            f'  <rect x="18" y="{y_card:.1f}" width="{svg_w - 36}" height="{cd.h:.1f}" rx="5" '
            'fill="#ffffff" stroke="#cbd5e1" stroke-width="1.3" />'
        )
        parts.append(
            f'  <rect x="18" y="{y_card:.1f}" width="{svg_w - 36}" height="{cd.header_h:.1f}" '
            f'rx="5" fill="{cd.role_col}" />'
        )
        for i, ln in enumerate(cd.h1):
            parts.append(
                f'  <text x="30" y="{y_card + 15 + i * 13.0:.1f}" class="whdr" '
                f'style="font-size:{cd.h1_fs}px;">{esc(ln)}</text>'
            )
        for i, ln in enumerate(cd.h2):
            parts.append(
                f'  <text x="30" y="{y_card + cd.h2_y0 + i * 10.5:.1f}" class="wsub" '
                f'style="font-size:{cd.h2_fs}px;">{esc(ln)}</text>'
            )
        parts.append(
            f'  <text x="{svg_w - 30}" y="{y_card + 15:.1f}" text-anchor="end" class="whdr">'
            f"{esc(cd.r1)}</text>"
        )
        parts.append(
            f'  <text x="{svg_w - 30}" y="{y_card + 28:.1f}" text-anchor="end" class="wsub">'
            f"{esc(cd.r2)}</text>"
        )
        _render_board_elevation_svg(
            parts, brd, cd, y_card=y_card, left_x=left_x, left_w=left_w
        )
        _render_board_door_directory_svg(
            parts, cd, y_card=y_card, right_x=right_x, right_w=right_w
        )
        y_card += cd.h + 18.0

    parts.append("</svg>")
    return "\n".join(parts)


def render_board_designs_dxf(result: Phase2DesignResult | ElectricalDesignResult) -> Drawing:
    """Build pristine R2018 DXF Sheet E-204 Main & Secondary Distribution Board Designs."""
    design = _unwrap(result)
    b = design.building
    boards = design.din_rail_boards

    doc = new_dxf("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1

    for lname, color in (
        ("E-TITLE", 7),
        ("E-DB-FRAME", 4),
        ("E-DIN-RAIL", 8),
        ("E-DIN-INCOMER", 1),
        ("E-DIN-FEEDER", 6),
        ("E-DIN-RCBO", 5),
        ("E-DIN-SPARE", 9),
        ("E-DB-TEXT", 7),
    ):
        if lname not in doc.layers:
            doc.layers.add(lname, color=color)

    msp = doc.modelspace()
    w_tot = 420.0

    def _cols_lines(lr_vals: tuple[str, ...], widths: tuple[int, ...]) -> list[str]:
        """Fixed-pitch door-directory row; long cells wrap onto continuation lines."""
        wrapped = [wrap_text_chars(v, w) for v, w in zip(lr_vals, widths, strict=True)]
        n_ln = max(len(c) for c in wrapped)
        out: list[str] = []
        for li in range(n_ln):
            segs = [
                (c[li] if li < len(c) else "").ljust(w)
                for c, w in zip(wrapped, widths, strict=True)
            ]
            out.append(" ".join(segs).rstrip())
        return out

    door_widths = (6, 8, 3, 24, 16, 34)
    title_lines = wrap_text_chars(
        f"E-204: MAIN & SECONDARY DISTRIBUTION BOARD DESIGNS — {b.project_name}",
        int((w_tot - 16.0) / (3.8 * 0.62)),
    )
    hdr_h = 13.0 + (len(title_lines) - 1) * 5.6
    # Compute every board block (full-length wrapped text) so all boards stack cleanly
    blocks: list[tuple[list[str], list[str], list[list[str]], float, float]] = []
    for brd in boards:
        role_lbl = _ROLE_META.get(brd.board_role, ("BOARD", "#1b365d"))[0]
        l1 = wrap_text_chars(
            f"{brd.board_id} ({brd.floor}) [{role_lbl}] | {brd.standard_ref} | "
            f"{brd.enclosure_dims_mm} | {brd.ip_rating} | {brd.busbar_rating_a}A Cu",
            int((w_tot - 18.0) / (2.3 * 0.62)),
        )
        l2 = wrap_text_chars(
            f"Supply: {brd.incoming_supply_spec} | "
            f"Thermal: {brd.power_dissipation_w:.1f}/{brd.max_enclosure_dissipation_w:.0f}W "
            f"({'PASS' if brd.thermal_pass else 'CHECK'})",
            int((w_tot - 18.0) / (1.8 * 0.62)),
        )
        head_extra = (len(l1) - 1) * 3.4 + (len(l2) - 1) * 2.8
        door = [
            _cols_lines(
                (
                    f"R{lr.rail_no}.{lr.way_no:02d}",
                    lr.circuit_id,
                    lr.phase,
                    lr.device_spec,
                    lr.cable_spec,
                    lr.description,
                ),
                door_widths,
            )
            for lr in brd.door_legend
        ]
        n_door_lines = sum(len(d) for d in door)
        elev_h = 28.0 + max(1, len(brd.rows)) * 18.0 + 18.0
        tbl_h = 22.0 + max(1, n_door_lines) * 5.2 + 8.0
        blocks.append((l1, l2, door, head_extra, head_extra + max(elev_h, tbl_h)))
    h_tot = max(290.0, hdr_h + 15.0 + sum(bl[4] + 10.0 for bl in blocks) + 12.0)

    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    for i, t_ln in enumerate(title_lines):
        msp.add_text(t_ln, dxfattribs={"layer": "E-TITLE", "height": 3.8}).set_placement(
            (8.0, h_tot - 9.0 - i * 5.6)
        )
    msp.add_line((0.0, h_tot - hdr_h), (w_tot, h_tot - hdr_h), dxfattribs={"layer": "E-TITLE"})

    y_top = h_tot - hdr_h - 5.0
    for brd, (l1, l2, door, head_extra, bh) in zip(boards, blocks, strict=True):
        y_bot = y_top - bh
        msp.add_lwpolyline(
            [(6.0, y_bot), (w_tot - 6.0, y_bot), (w_tot - 6.0, y_top), (6.0, y_top)],
            close=True,
            dxfattribs={"layer": "E-DB-FRAME"},
        )
        hy = y_top - 5.2
        for t_ln in l1:
            msp.add_text(t_ln, dxfattribs={"layer": "E-TITLE", "height": 2.3}).set_placement(
                (9.0, hy)
            )
            hy -= 3.4
        hy = y_top - 9.6 - (len(l1) - 1) * 3.4
        for t_ln in l2:
            msp.add_text(t_ln, dxfattribs={"layer": "E-DB-TEXT", "height": 1.8}).set_placement(
                (9.0, hy)
            )
            hy -= 2.8

        # Left side: scaled DIN rails & individual module rectangles
        rx0, rw = 10.0, 188.0
        ry = y_top - 16.0 - head_extra
        for row in brd.rows:
            cap_m = max(12, row.modules_capacity)
            mm_per_m = rw / float(cap_m)
            msp.add_line(
                (rx0, ry - 6.5), (rx0 + rw, ry - 6.5), dxfattribs={"layer": "E-DIN-RAIL"}
            )
            for sl in row.slots:
                sx0 = rx0 + (sl.slot_start_m - 1) * mm_per_m + 0.25
                sx1 = rx0 + (sl.slot_start_m - 1 + sl.width_m) * mm_per_m - 0.25
                lyr = (
                    "E-DIN-SPARE"
                    if sl.kind == "spare"
                    else (
                        "E-DIN-INCOMER"
                        if sl.kind in ("incomer", "rcd_s", "spd", "ats")
                        else ("E-DIN-FEEDER" if sl.kind in ("feeder", "pv_isol") else "E-DIN-RCBO")
                    )
                )
                msp.add_lwpolyline(
                    [(sx0, ry - 13.0), (sx1, ry - 13.0), (sx1, ry), (sx0, ry)],
                    close=True,
                    dxfattribs={"layer": lyr},
                )
                # Full module code; text height fitted to the module width (never cut)
                lbl = sl.code if sl.kind != "spare" else "SPARE"
                t_h = min(1.4, max(0.45, (sx1 - sx0 - 1.0) / max(1.0, len(lbl) * 0.62)))
                msp.add_text(
                    lbl, dxfattribs={"layer": "E-DB-TEXT", "height": round(t_h, 2)}
                ).set_placement((sx0 + 0.5, ry - 5.2))
            ry -= 17.5

        # Right side: BS 7671 Door Schedule (wrapped continuation lines, full text)
        tx = 206.0
        ty = y_top - 15.0 - head_extra
        msp.add_text(
            "BS 7671 REG 514.9.1 DOOR CIRCUIT DIRECTORY",
            dxfattribs={"layer": "E-TITLE", "height": 2.0},
        ).set_placement((tx, ty))
        ty -= 5.0
        for d_lines in door:
            for d_ln in d_lines:
                msp.add_text(
                    d_ln, dxfattribs={"layer": "E-DB-TEXT", "height": 1.65}
                ).set_placement((tx, ty))
                ty -= 5.2

        y_top = y_bot - 10.0

    psp = doc.layouts.new("E204-BOARDS-A1")
    psp.page_setup(size=(841.0, 594.0), margins=(10.0, 10.0, 10.0, 10.0), units="mm")
    psp.add_viewport(
        center=(420.5, 297.0),
        size=(815.0, 568.0),
        view_center_point=(w_tot * 0.5, h_tot * 0.5),
        view_height=h_tot + 8.0,
        dxfattribs={"layer": "E-TITLE"},
    )
    return doc


def export_board_designs_deliverables(
    result: Phase2DesignResult | ElectricalDesignResult,
    out_dir: Path,
    *,
    export_pdf: bool = False,
) -> dict[str, Path]:
    """Write E-204 Main & Secondary Board Designs DXF, SVG, and optional WeasyPrint PDF."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_p = out_dir / "E204_board_designs.dxf"
    svg_p = out_dir / "E204_board_designs.svg"
    render_board_designs_dxf(result).saveas(str(dxf_p))
    svg_p.write_text(render_board_designs_svg(result), encoding="utf-8")
    out: dict[str, Path] = {"e204_boards_dxf": dxf_p, "e204_boards_svg": svg_p}
    if export_pdf:
        pdf_p, _flag = export_svg_sheet_to_pdf(svg_p, out_dir / "E204_board_designs.pdf")
        if pdf_p is not None and pdf_p.exists():
            out["e204_boards_pdf"] = pdf_p
    return out
