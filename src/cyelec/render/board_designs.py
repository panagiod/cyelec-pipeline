"""E-204 Main & Secondary Distribution Board Designs — Elevations, IEC 61439 Specs & Door Schedules.

Generates buildable panel-assembly drawings (E204_board_designs.svg, .dxf & .pdf) for every
Main Board (MDB / DB1) and Secondary Board (DB-GF, DB-1F, DB-POOL IP65, DB-EPS Backup,
SDB-LP, SDB-HVAC), including true-scale 18 mm DIN-rail / MCCB module front elevations,
IEC 60446 phase striping, IEC 60890 thermal dissipation checks, and printable BS 7671
Reg 514.9.1 Inside-Door Circuit Directories.
"""

from __future__ import annotations

from html import escape as esc
from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import (
    BoardModuleSlot,
    ElectricalDesignResult,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.pdf_export import export_svg_sheet_to_pdf


def _unwrap(result: Phase2DesignResult | ElectricalDesignResult) -> ElectricalDesignResult:
    return result.design if isinstance(result, Phase2DesignResult) else result


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


def _short_slot_lines(sl: BoardModuleSlot, slot_px: float) -> tuple[str, str]:
    """Return compact (top_code, bottom_rating) guaranteed to fit inside slot_px."""
    max_ch = max(2, int((slot_px - 4.0) / 4.3))
    if sl.kind == "spare":
        top = "SPARE" if max_ch >= 5 else "SP"
        bot = f"{sl.width_m}M" if max_ch >= 3 else ""
        return top[:max_ch], bot[:max_ch]
    code = sl.code
    if len(code) > max_ch:
        code = code.replace("Q-DB-", "Q").replace("Q-", "Q")[:max_ch]
    lbl = sl.label
    if len(lbl) > max_ch:
        lbl = lbl.replace(" ", "").replace("100mA-S", "100S").replace("T1+2", "T12")[:max_ch]
    return code, lbl


def _trunc(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)] + "…"


def render_board_designs_svg(result: Phase2DesignResult | ElectricalDesignResult) -> str:
    """Render Sheet E-204 Main & Secondary Distribution Board Front Elevations & Door Schedules."""
    design = _unwrap(result)
    b = design.building
    eac = design.eac_compliance
    boards = design.din_rail_boards

    svg_w = 1240
    left_x = 24.0
    left_w = 590.0
    right_x = 630.0
    right_w = 586.0

    # Pre-compute exact height of every board card so nothing ever clips or collides
    card_heights: list[float] = []
    for brd in boards:
        n_rows = max(1, len(brd.rows))
        # Left elevation height: header(42) + spec_bar(32) + term_bar(24) + rails(n*62) + footer(74)
        elev_h = 42.0 + 32.0 + 24.0 + n_rows * 62.0 + 78.0
        # Right door schedule height: header(42) + tbl_hdr(22) + rows*16.5 + pad(18)
        tbl_h = 42.0 + 24.0 + max(1, len(brd.door_legend)) * 16.5 + 22.0
        card_heights.append(max(elev_h, tbl_h))

    svg_h = int(max(760.0, 96.0 + sum(h + 18.0 for h in card_heights) + 34.0))
    main_cnt = sum(1 for bd in boards if bd.board_role == "main")
    sec_cnt = len(boards) - main_cnt
    sec_names = ", ".join(bd.board_id for bd in boards if bd.board_role != "main") or "None needed"

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
        (
            '  <text x="24" y="34" class="hdr">'
            "E-204 MAIN &amp; SECONDARY DISTRIBUTION BOARD DESIGNS — "
            f"{esc(_trunc(b.project_name, 62))}</text>"
        ),
        (
            f'  <text x="24" y="51" class="sub">Supply: {esc(eac.recommended_supply_tier)} | '
            f"Earthing: {esc(design.earthing_system.value)} | "
            f"Main: {main_cnt} | Secondary: {sec_cnt} ({esc(_trunc(sec_names, 36))})</text>"
        ),
        # Compact Phase & Device Color Key right-aligned on subtitle row (never collides with title)
        '  <rect x="856" y="43" width="12" height="8" fill="#92400e" />',
        '  <text x="872" y="50" class="xs">L1</text>',
        '  <rect x="896" y="43" width="12" height="8" fill="#1e293b" />',
        '  <text x="912" y="50" class="xs">L2</text>',
        '  <rect x="936" y="43" width="12" height="8" fill="#64748b" />',
        '  <text x="952" y="50" class="xs">L3</text>',
        '  <rect x="976" y="43" width="12" height="8" fill="#1d4ed8" />',
        '  <text x="992" y="50" class="xs">3P</text>',
        '  <rect x="1020" y="43" width="12" height="8" fill="#fee2e2" stroke="#dc2626" />',
        '  <text x="1036" y="50" class="xs">Incomer</text>',
        '  <rect x="1088" y="43" width="12" height="8" fill="#ede9fe" stroke="#7c3aed" />',
        '  <text x="1104" y="50" class="xs">Feeder</text>',
        '  <rect x="1152" y="43" width="12" height="8" fill="#f1f5f9" stroke="#94a3b8" '
        'stroke-dasharray="2,2" />',
        '  <text x="1168" y="50" class="xs">Spare</text>',
        f'  <line x1="10" y1="62" x2="{svg_w - 10}" y2="62" stroke="#cbd5e1" stroke-width="1.4" />',
    ]

    y_card = 74.0
    for brd, ch in zip(boards, card_heights, strict=True):
        role_lbl, role_col = _ROLE_META.get(brd.board_role, ("BOARD", "#1b365d"))
        parent_txt = (
            f"Fed from {brd.parent_board_id}" if brd.parent_board_id else "EAC Service Origin"
        )

        # Outer card frame
        parts.append(
            f'  <rect x="18" y="{y_card:.1f}" width="{svg_w - 36}" height="{ch:.1f}" rx="5" '
            'fill="#ffffff" stroke="#cbd5e1" stroke-width="1.3" />'
        )
        # Card header bar
        parts.append(
            f'  <rect x="18" y="{y_card:.1f}" width="{svg_w - 36}" height="34" rx="5" '
            f'fill="{role_col}" />'
        )
        parts.append(
            f'  <text x="30" y="{y_card + 15:.1f}" class="whdr">'
            f"{esc(brd.board_id)} ({esc(brd.floor)}) — {esc(role_lbl)} | "
            f"{esc(brd.standard_ref)}</text>"
        )
        parts.append(
            f'  <text x="30" y="{y_card + 28:.1f}" class="wsub">'
            f"{esc(parent_txt)} · Supply: {esc(_trunc(brd.incoming_supply_spec, 96))}</text>"
        )
        parts.append(
            f'  <text x="{svg_w - 30}" y="{y_card + 15:.1f}" text-anchor="end" class="whdr">'
            f"{brd.total_modules_used}/{brd.total_modules_capacity}M ({brd.spare_pct:.0f}% spare)"
            "</text>"
        )
        parts.append(
            f'  <text x="{svg_w - 30}" y="{y_card + 28:.1f}" text-anchor="end" class="wsub">'
            f"Dims: {esc(brd.enclosure_dims_mm)} | {esc(brd.ip_rating)} | "
            f"Busbar: {brd.busbar_rating_a}A Cu</text>"
        )

        # ---- LEFT COLUMN: PHYSICAL FRONT ELEVATION ----
        ex = left_x
        ey = y_card + 42.0
        ew = left_w
        n_rows = max(1, len(brd.rows))
        cab_h = 26.0 + n_rows * 62.0 + 18.0

        parts.append(
            f'  <text x="{ex:.1f}" y="{ey + 10:.1f}" class="sec">'
            "PHYSICAL FRONT ELEVATION (TRUE 18mm MODULE SCALE)</text>"
        )
        parts.append(
            f'  <text x="{ex + ew:.1f}" y="{ey + 10:.1f}" text-anchor="end" class="xs">'
            f"Ipf {brd.incoming_ipf_ka:.2f} kA &lt;= Icu {brd.min_device_icu_ka:.0f} kA</text>"
        )

        # Metallic cabinet bezel
        cy0 = ey + 16.0
        cab_fill = "#f0fdfa" if brd.board_role == "secondary_pool" else "#f8fafc"
        cab_stroke = "#0f766e" if brd.board_role == "secondary_pool" else "#475569"
        parts.append(
            f'  <rect x="{ex:.1f}" y="{cy0:.1f}" width="{ew:.1f}" height="{cab_h:.1f}" rx="4" '
            f'fill="{cab_fill}" stroke="{cab_stroke}" stroke-width="1.8" />'
        )
        # Top Brass Neutral (Blue) & Earth/PE (Green) Terminal Bars
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

        # DIN rails & individual scaled modules
        rail_x0 = ex + 26.0
        rail_w = ew - 38.0
        ry = cy0 + 26.0
        for row in brd.rows:
            cap_m = max(12, row.modules_capacity)
            # Row label left margin
            parts.append(
                f'  <text x="{ex + 12:.1f}" y="{ry + 28:.1f}" text-anchor="middle" '
                f'class="th">R{row.row_no}</text>'
            )
            # Metallic DIN rail strip behind modules
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
                # IEC 60446 phase indicator stripe along top edge of active devices
                if sl.kind != "spare":
                    p_col = _PHASE_COLOR.get(sl.phase, "#94a3b8")
                    parts.append(
                        f'  <rect x="{sx + 0.6:.1f}" y="{ry + 0.6:.1f}" '
                        f'width="{max(2.0, sw - 1.2):.1f}" height="4.5" fill="{p_col}" />'
                    )
                    # Miniature breaker toggle handle graphic
                    if sl.kind not in ("meter", "knx"):
                        tw = min(14.0, max(5.0, sw * 0.42))
                        tx = sx + (sw - tw) / 2.0
                        parts.append(
                            f'  <rect x="{tx:.1f}" y="{ry + 20.5:.1f}" width="{tw:.1f}" '
                            'height="6.5" rx="1" fill="#1e293b" />'
                        )
                top_t, bot_t = _short_slot_lines(sl, sw)
                cx_m = sx + sw / 2.0
                if top_t:
                    parts.append(
                        f'  <text x="{cx_m:.1f}" y="{ry + 15.5:.1f}" text-anchor="middle" '
                        f'class="mcode">{esc(top_t)}</text>'
                    )
                if bot_t:
                    parts.append(
                        f'  <text x="{cx_m:.1f}" y="{ry + 41.5:.1f}" text-anchor="middle" '
                        f'class="mlbl">{esc(bot_t)}</text>'
                    )
            ry += 62.0

        # Bottom gland plate strip inside cabinet
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

        # Thermal, Phase Balance & Terminal Spec Footer below cabinet
        fy = cy0 + cab_h + 13.0
        th_cls = "ok" if brd.thermal_pass else "warn"
        th_txt = "PASS" if brd.thermal_pass else "UPGRADE ENCLOSURE"
        parts.append(
            f'  <text x="{ex:.1f}" y="{fy:.1f}" class="{th_cls}">'
            f"IEC 60890 Thermal (40°C): P_loss = {brd.power_dissipation_w:.1f} W &lt;= "
            f"P_max = {brd.max_enclosure_dissipation_w:.0f} W [{th_txt}]</text>"
        )
        pl = brd.phase_loads_a
        ph_str = (
            f"L1: {pl.get('L1', 0.0):.1f}A | L2: {pl.get('L2', 0.0):.1f}A | "
            f"L3: {pl.get('L3', 0.0):.1f}A"
            if pl
            else "Feeder Bus"
        )
        parts.append(
            f'  <text x="{ex + ew:.1f}" y="{fy:.1f}" text-anchor="end" class="xs">'
            f"Board Load: {esc(ph_str)}</text>"
        )
        parts.append(
            f'  <text x="{ex:.1f}" y="{fy + 14:.1f}" class="xs">'
            f"Bars: {esc(_trunc(brd.terminal_bars_spec, 96))}</text>"
        )
        parts.append(
            f'  <text x="{ex:.1f}" y="{fy + 27:.1f}" class="xs">'
            f"Selectivity: {esc(_trunc(brd.selectivity_note, 96))}</text>"
        )

        # ---- RIGHT COLUMN: BS 7671 REG 514.9.1 INSIDE-DOOR CIRCUIT DIRECTORY ----
        tx0 = right_x
        ty0 = y_card + 42.0
        parts.append(
            f'  <text x="{tx0:.1f}" y="{ty0 + 10:.1f}" class="sec">'
            "BS 7671 REG 514.9.1 INSIDE-DOOR CIRCUIT DIRECTORY</text>"
        )
        # Table header
        th_y = ty0 + 16.0
        parts.append(
            f'  <rect x="{tx0:.1f}" y="{th_y:.1f}" width="{right_w:.1f}" height="18" '
            'fill="#e2e8f0" stroke="#cbd5e1" stroke-width="0.8" />'
        )
        col_xs = (tx0 + 6, tx0 + 34, tx0 + 78, tx0 + 106, tx0 + 244, tx0 + 344)
        for cx, htxt in zip(
            col_xs, ("Way", "ID", "Ph", "Protective Device", "Cable", "Load / Room"), strict=True
        ):
            parts.append(
                f'  <text x="{cx:.1f}" y="{th_y + 12.2:.1f}" class="th">{htxt}</text>'
            )
        row_y = th_y + 18.0
        for idx_l, lr in enumerate(brd.door_legend):
            bg = "#f8fafc" if idx_l % 2 == 1 else "#ffffff"
            parts.append(
                f'  <rect x="{tx0:.1f}" y="{row_y:.1f}" width="{right_w:.1f}" height="16.5" '
                f'fill="{bg}" stroke="#e2e8f0" stroke-width="0.5" />'
            )
            ty_txt = row_y + 11.4
            vals = (
                f"R{lr.rail_no}.{lr.way_no}",
                _trunc(lr.circuit_id, 7),
                lr.phase,
                _trunc(lr.device_spec, 26),
                _trunc(lr.cable_spec, 18),
                _trunc(lr.description, 44),
            )
            for cx, vtxt in zip(col_xs, vals, strict=True):
                parts.append(
                    f'  <text x="{cx:.1f}" y="{ty_txt:.1f}" class="td">{esc(vtxt)}</text>'
                )
            row_y += 16.5

        y_card += ch + 18.0

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
    # Compute total drawing height dynamically so all boards stack cleanly with zero collision
    board_hs: list[float] = []
    for brd in boards:
        elev_h = 28.0 + max(1, len(brd.rows)) * 18.0 + 18.0
        tbl_h = 22.0 + max(1, len(brd.door_legend)) * 5.2 + 8.0
        board_hs.append(max(elev_h, tbl_h))
    h_tot = max(290.0, 28.0 + sum(h + 10.0 for h in board_hs) + 12.0)

    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text(
        f"E-204: MAIN & SECONDARY DISTRIBUTION BOARD DESIGNS — {b.project_name[:42]}",
        dxfattribs={"layer": "E-TITLE", "height": 3.8},
    ).set_placement((8.0, h_tot - 9.0))
    msp.add_line((0.0, h_tot - 13.0), (w_tot, h_tot - 13.0), dxfattribs={"layer": "E-TITLE"})

    y_top = h_tot - 18.0
    for brd, bh in zip(boards, board_hs, strict=True):
        y_bot = y_top - bh
        msp.add_lwpolyline(
            [(6.0, y_bot), (w_tot - 6.0, y_bot), (w_tot - 6.0, y_top), (6.0, y_top)],
            close=True,
            dxfattribs={"layer": "E-DB-FRAME"},
        )
        role_lbl = _ROLE_META.get(brd.board_role, ("BOARD", "#1b365d"))[0]
        msp.add_text(
            (
                f"{brd.board_id} ({brd.floor}) [{role_lbl}] | {brd.standard_ref[:40]} | "
                f"{brd.enclosure_dims_mm} | {brd.ip_rating} | {brd.busbar_rating_a}A Cu"
            )[:115],
            dxfattribs={"layer": "E-TITLE", "height": 2.3},
        ).set_placement((9.0, y_top - 5.2))
        msp.add_text(
            (
                f"Supply: {brd.incoming_supply_spec[:68]} | "
                f"Thermal: {brd.power_dissipation_w:.1f}/{brd.max_enclosure_dissipation_w:.0f}W "
                f"({'PASS' if brd.thermal_pass else 'CHECK'})"
            )[:118],
            dxfattribs={"layer": "E-DB-TEXT", "height": 1.8},
        ).set_placement((9.0, y_top - 9.6))

        # Left side: scaled DIN rails & individual module rectangles
        rx0, rw = 10.0, 188.0
        ry = y_top - 16.0
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
                lbl = sl.code[:6] if sl.kind != "spare" else "SP"
                msp.add_text(
                    lbl, dxfattribs={"layer": "E-DB-TEXT", "height": 1.4}
                ).set_placement((sx0 + 0.8, ry - 5.2))
            ry -= 17.5

        # Right side: BS 7671 Door Schedule
        tx = 206.0
        ty = y_top - 15.0
        msp.add_text(
            "BS 7671 REG 514.9.1 DOOR CIRCUIT DIRECTORY",
            dxfattribs={"layer": "E-TITLE", "height": 2.0},
        ).set_placement((tx, ty))
        ty -= 5.0
        for lr in brd.door_legend:
            line = (
                f"R{lr.rail_no}.{lr.way_no:02d} {lr.circuit_id:6s} {lr.phase:3s} "
                f"{lr.device_spec[:24]:24s} {lr.cable_spec[:16]:16s} {lr.description[:32]}"
            )
            msp.add_text(
                line, dxfattribs={"layer": "E-DB-TEXT", "height": 1.65}
            ).set_placement((tx, ty))
            ty -= 5.0

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
