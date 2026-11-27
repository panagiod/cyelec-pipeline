"""E-202 Vertical Riser Diagram & Physical 18 mm DIN-Rail DB Elevation + Selectivity (DXF + SVG)."""

from __future__ import annotations

from html import escape as esc
from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import DinRailBoardLayout, ElectricalDesignResult
from cyelec.models.spatial import Phase2DesignResult


def _unwrap(result: Phase2DesignResult | ElectricalDesignResult) -> ElectricalDesignResult:
    return result.design if isinstance(result, Phase2DesignResult) else result


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
    w_tot, h_tot = 420.0, 290.0
    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text(
        f"E-202: VERTICAL RISER DIAGRAM & PHYSICAL 18MM DIN-RAIL DB LAYOUT — {b.project_name[:38]}",
        dxfattribs={"layer": "E-TITLE", "height": 4.0},
    ).set_placement((8.0, h_tot - 10.0))
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
            msp.add_text(
                mdb_lbl[:30], dxfattribs={"layer": "E-RISER-PWR", "height": 2.0}
            ).set_placement((pwr_x - 22.0, ly + 5.0))
            if aux_boards:
                aux_lbl = "AUX: " + ", ".join(bd.board_id for bd in aux_boards)
                msp.add_text(
                    aux_lbl[:34], dxfattribs={"layer": "E-RISER-PV", "height": 1.8}
                ).set_placement((elv_x - 18.0, ly + 5.0))
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
                lbl = ", ".join(bd.board_id for bd in aux_boards[:2])
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
            msp.add_text(
                lbl[:30], dxfattribs={"layer": "E-RISER-PWR", "height": 2.0}
            ).set_placement((pwr_x - 20.0, ly + 5.0))
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
    for brd in boards[:4]:
        msp.add_text(
            f"{brd.board_id} ({brd.floor}) — {brd.total_modules_used}M / "
            f"{brd.total_modules_capacity}M ({brd.spare_pct:.0f}% spare)",
            dxfattribs={"layer": "E-TITLE", "height": 2.4},
        ).set_placement((214.0, y_cur))
        y_cur -= 5.8
        msp.add_text(
            f"Enclosure: {brd.enclosure_spec[:68]}",
            dxfattribs={"layer": "E-DIN-SPARE", "height": 1.9},
        ).set_placement((214.0, y_cur))
        y_cur -= 5.2
        msp.add_text(
            f"Ipf={brd.incoming_ipf_ka:.1f}kA <= Icu={brd.min_device_icu_ka:.0f}kA | "
            f"{brd.selectivity_note[:56]}",
            dxfattribs={
                "layer": "E-RISER-EARTHING" if brd.selectivity_pass else "E-RISER-PWR",
                "height": 1.9,
            },
        ).set_placement((214.0, y_cur))
        y_cur -= 6.5

        for row in brd.rows[:4]:
            # Draw scaled DIN rail bar (width 186mm)
            rx0, rx1 = 216.0, 404.0
            msp.add_lwpolyline(
                [(rx0, y_cur - 4.0), (rx1, y_cur - 4.0), (rx1, y_cur + 2.5), (rx0, y_cur + 2.5)],
                close=True,
                dxfattribs={"layer": "E-DIN-RAIL"},
            )
            used_w = (rx1 - rx0) * (row.modules_used / max(float(row.modules_capacity), 1.0))
            if used_w > 1.0:
                msp.add_line(
                    (rx0 + used_w, y_cur - 4.0),
                    (rx0 + used_w, y_cur + 2.5),
                    dxfattribs={"layer": "E-RISER-PWR"},
                )
            row_txt = _pack_din_row_label(
                row.row_no, row.modules_used, row.modules_capacity, row.items, 80
            )
            msp.add_text(
                row_txt, dxfattribs={"layer": "E-TITLE", "height": 1.7}
            ).set_placement((rx0 + 2.0, y_cur - 2.0))
            y_cur -= 8.8
        y_cur -= 4.5
        if y_cur < 20.0:
            break

    psp = doc.layouts.new("E202-RISER-DB-A1")
    psp.page_setup(size=(841.0, 594.0), margins=(10.0, 10.0, 10.0, 10.0), units="mm")
    psp.add_viewport(
        center=(420.5, 297.0),
        size=(815.0, 568.0),
        view_center_point=(w_tot * 0.5, h_tot * 0.5),
        view_height=h_tot + 8.0,
        dxfattribs={"layer": "E-TITLE"},
    )
    return doc


def _wrap_riser_text(text: str, max_chars: int = 76) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _pack_din_row_label(
    row_no: int, used: int, cap: int, items: list[str], max_chars: int = 86
) -> str:
    """Pack whole DIN-rail circuit items into a single crisp row label without mid-word cuts."""
    prefix = f"Row {row_no} ({used}/{cap}M): "
    chosen: list[str] = []
    for idx, itm in enumerate(items):
        rem = len(items) - idx - 1
        suffix = f" (+{rem} more)" if rem > 0 else ""
        cand = prefix + ", ".join([*chosen, itm]) + suffix
        if len(cand) <= max_chars or not chosen:
            chosen.append(itm)
        else:
            break
    rem_final = len(items) - len(chosen)
    out = prefix + ", ".join(chosen)
    if rem_final > 0:
        out += f" (+{rem_final} more)"
    return out


def render_riser_and_db_svg(result: Phase2DesignResult | ElectricalDesignResult) -> str:
    """Render E-202 Vertical Riser Diagram & Physical 18mm DIN-Rail DB Elevation as SVG."""
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

    # Pre-compute exact right-pane height so cards never clip at the bottom
    board_blocks: list[tuple[DinRailBoardLayout, list[str], float]] = []
    total_boards_h = 128.0
    for brd in boards:
        sel_full = (
            f"Ipf={brd.incoming_ipf_ka:.1f} kA <= Icu={brd.min_device_icu_ka:.0f} kA | "
            f"{brd.selectivity_note}"
        )
        sel_lines = _wrap_riser_text(sel_full, max_chars=74)
        bh = 56.0 + len(sel_lines) * 14.0 + len(brd.rows) * 31.0 + 12.0
        board_blocks.append((brd, sel_lines, bh))
        total_boards_h += bh + 16.0

    svg_w = 1190
    svg_h = int(max(760, total_boards_h + 30.0))
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
        (
            '  <text x="26" y="38" class="hdr">'
            f"E-202 VERTICAL RISER &amp; 18mm DIN-RAIL DB SCHEDULE — {esc(b.project_name)}</text>"
        ),
        (
            f'  <text x="26" y="55" class="sub">Supply: {esc(eac.recommended_supply_tier)} | '
            f"Earthing: {esc(design.earthing_system.value)} | "
            f"Ipf: {design.prospective_fault_current_ka:.1f} kA | Boards: {len(boards)} "
            "(See E-204 for Full Module Elevations &amp; Door Schedules)</text>"
        ),
        f'  <line x1="12" y1="66" x2="{svg_w - 12}" y2="66" stroke="#cbd5e0" stroke-width="1.5" />',
        (
            '  <text x="28" y="90" class="sec">'
            "A. VERTICAL RISER SCHEMATIC (POWER / ELV / PV / EARTHING)</text>"
        ),
        (
            f'  <line x1="560" y1="66" x2="560" y2="{svg_h - 16}" '
            'stroke="#cbd5e0" stroke-width="1.5" />'
        ),
    ]

    y_top, y_bot = 135.0, min(680.0, svg_h - 60.0)
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

    for idx, lvl in enumerate(levels):
        # Bottom-up rendering (SITE at bottom, ROOF at top)
        ly = y_bot - idx * step_y
        parts.append(
            f'  <line x1="26" y1="{ly:.1f}" x2="548" y2="{ly:.1f}" '
            'stroke="#e2e8f0" stroke-width="1.5" stroke-dasharray="4,4" />'
        )
        parts.append(
            f'  <text x="28" y="{ly - 6:.1f}" class="lbl" style="fill:#4a5568;">{esc(lvl)}</text>'
        )
        if lvl == "SITE / STREET":
            parts.append(
                f'  <rect x="{pwr_x - 68}" y="{ly - 26}" width="136" height="24" '
                'rx="4" fill="#c53030" />'
            )
            parts.append(
                f'  <text x="{pwr_x}" y="{ly - 11}" text-anchor="middle" class="wlbl">'
                f"EAC METER ({esc(tier_short)})</text>"
            )
            parts.append(
                f'  <rect x="{elv_x - 50}" y="{ly - 26}" width="100" height="24" '
                'rx="4" fill="#2b6cb0" />'
            )
            parts.append(
                f'  <text x="{elv_x}" y="{ly - 11}" text-anchor="middle" class="wlbl">'
                "CYTA FIBER PIT</text>"
            )
            parts.append(
                f'  <rect x="{ear_x - 48}" y="{ly - 26}" width="96" height="24" '
                'rx="4" fill="#2f855a" />'
            )
            parts.append(
                f'  <text x="{ear_x}" y="{ly - 11}" text-anchor="middle" class="wlbl">'
                "EARTH ELECTRODE</text>"
            )
        elif lvl.startswith("MAIN ("):
            mdb_lbl = f"{b.db_anchor.id} ({eac.recommended_main_breaker_a}A MAIN)"
            parts.append(
                f'  <rect x="{pwr_x - 72}" y="{ly - 26}" width="144" height="24" rx="4" '
                'fill="#1b365d" stroke="#0f172a" stroke-width="1.6" />'
            )
            parts.append(
                f'  <text x="{pwr_x}" y="{ly - 11}" text-anchor="middle" class="wlbl">'
                f"{esc(mdb_lbl)}</text>"
            )
            if aux_boards:
                aux_lbl = " / ".join(bd.board_id for bd in aux_boards[:3])
                parts.append(
                    f'  <line x1="{pwr_x + 72}" y1="{ly - 14}" x2="{elv_x - 66}" y2="{ly - 14}" '
                    'stroke="#0f766e" stroke-width="2" stroke-dasharray="4,2" />'
                )
                parts.append(
                    f'  <rect x="{elv_x - 66}" y="{ly - 26}" width="132" height="24" rx="4" '
                    'fill="#f0fdfa" stroke="#0f766e" stroke-width="1.4" />'
                )
                parts.append(
                    f'  <text x="{elv_x}" y="{ly - 11}" text-anchor="middle" '
                    f'class="lbl" style="fill:#0f766e;">{esc(aux_lbl)}</text>'
                )
            parts.append(
                f'  <rect x="{ear_x - 48}" y="{ly - 26}" width="96" height="24" rx="4" '
                'fill="#f0fff4" stroke="#2f855a" stroke-width="1.4" />'
            )
            parts.append(
                f'  <text x="{ear_x}" y="{ly - 11}" text-anchor="middle" '
                'class="lbl" style="fill:#22543d;">MET / 16mm² MEC</text>'
            )
        elif lvl == "ROOF":
            pv_s = (
                f"PV {b.pv_capacity_kva:g}kVA + ISOL"
                if b.pv_capacity_kva > 0
                else "PV CONDUIT + SOLAR WH"
            )
            parts.append(
                f'  <rect x="{pwr_x - 68}" y="{ly - 24}" width="136" height="22" '
                'rx="4" fill="#d69e2e" />'
            )
            parts.append(
                f'  <text x="{pwr_x}" y="{ly - 10}" text-anchor="middle" class="wlbl">'
                f"{esc(pv_s)}</text>"
            )
            lps_s = (
                "LPS CLASS IV"
                if (design.lightning_risk and design.lightning_risk.lps_required)
                else "ROOF BONDING"
            )
            parts.append(
                f'  <rect x="{ear_x - 48}" y="{ly - 24}" width="96" height="22" '
                'rx="4" fill="#2f855a" />'
            )
            parts.append(
                f'  <text x="{ear_x}" y="{ly - 10}" text-anchor="middle" class="wlbl">'
                f"{esc(lps_s)}</text>"
            )
        else:
            sm = sm_by_floor.get(lvl)
            if sm:
                p_lbl = f"{sm.board_id} ({sm.cable_mm2:g}mm² {sm.breaker_in_a}A)"
            elif has_multi_boards and aux_boards:
                p_lbl = " + ".join(bd.board_id for bd in aux_boards[:2])
            else:
                p_lbl = f"{b.db_anchor.id} ({eac.recommended_main_breaker_a}A)"
            parts.append(
                f'  <rect x="{pwr_x - 72}" y="{ly - 26}" width="144" height="24" rx="4" '
                'fill="#fff5f5" stroke="#c53030" stroke-width="1.6" />'
            )
            parts.append(
                f'  <text x="{pwr_x}" y="{ly - 11}" text-anchor="middle" '
                f'class="lbl" style="fill:#9b2c2c;">{esc(p_lbl)}</text>'
            )
            e_lbl = "9U RACK + NVR + ALARM" if idx == first_hab_idx else f"WIFI AP + DATA ({lvl})"
            parts.append(
                f'  <rect x="{elv_x - 68}" y="{ly - 26}" width="136" height="24" rx="4" '
                'fill="#ebf8ff" stroke="#2b6cb0" stroke-width="1.4" />'
            )
            parts.append(
                f'  <text x="{elv_x}" y="{ly - 11}" text-anchor="middle" '
                f'class="lbl" style="fill:#2c5282;">{esc(e_lbl)}</text>'
            )
            g_lbl = "MET / 10mm² BOND" if idx == first_hab_idx else "PE RISER 10mm²"
            parts.append(
                f'  <rect x="{ear_x - 48}" y="{ly - 26}" width="96" height="24" rx="4" '
                'fill="#f0fff4" stroke="#2f855a" stroke-width="1.4" />'
            )
            parts.append(
                f'  <text x="{ear_x}" y="{ly - 11}" text-anchor="middle" '
                f'class="lbl" style="fill:#22543d;">{esc(g_lbl)}</text>'
            )

    # Right Pane: Physical 18mm DIN-Rail DB Summary (full scaled elevations on Sheet E-204)
    parts.append(
        '  <text x="580" y="90" class="sec">'
        "B. MAIN &amp; SECONDARY DB SUMMARY (FULL ELEVATIONS ON SHEET E-204)</text>"
    )
    y_b = 108.0
    for brd, sel_lines, box_h in board_blocks:
        parts.append(
            f'  <rect x="580" y="{y_b:.1f}" width="584" height="{box_h:.1f}" rx="6" '
            'fill="#f8fafc" stroke="#cbd5e0" stroke-width="1.5" />'
        )
        parent_tag = f" <- {brd.parent_board_id}" if brd.parent_board_id else " [MAIN]"
        parts.append(
            f'  <text x="594" y="{y_b + 19:.1f}" class="sec">'
            f"{esc(brd.board_id)} ({esc(brd.floor)}){esc(parent_tag)} — "
            f"{brd.total_modules_used}M / {brd.total_modules_capacity}M "
            f"({brd.spare_pct:.0f}% spare)</text>"
        )
        parts.append(
            f'  <text x="594" y="{y_b + 35:.1f}" class="txt">'
            f"{esc(brd.enclosure_spec)} | {esc(brd.enclosure_dims_mm)}</text>"
        )
        sel_col = "#2f855a" if brd.selectivity_pass else "#c53030"
        sy = y_b + 50.0
        for sline in sel_lines:
            parts.append(
                f'  <text x="594" y="{sy:.1f}" class="lbl" style="fill:{sel_col};">'
                f"{esc(sline)}</text>"
            )
            sy += 14.0
        ry = sy + 2.0
        for row in brd.rows:
            rail_w = 556.0
            used_w = rail_w * (row.modules_used / max(float(row.modules_capacity), 1.0))
            parts.append(
                f'  <rect x="594" y="{ry:.1f}" width="{rail_w:.1f}" height="24" rx="3" '
                'fill="#edf2f7" stroke="#a0aec0" stroke-width="1" />'
            )
            if used_w > 1.0:
                parts.append(
                    f'  <rect x="594" y="{ry:.1f}" width="{used_w:.1f}" height="24" rx="3" '
                    'fill="#bee3f8" stroke="#2b6cb0" stroke-width="1" />'
                )
            row_lbl = _pack_din_row_label(
                row.row_no, row.modules_used, row.modules_capacity, row.items, max_chars=84
            )
            parts.append(
                f'  <text x="602" y="{ry + 15:.1f}" class="txt">{esc(row_lbl)}</text>'
            )
            ry += 30.0
        y_b += box_h + 16.0

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
