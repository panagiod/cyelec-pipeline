"""Deterministic Single-Line Diagram (SLD) renderer outputting print-ready A3 SVG and R2018 DXF."""

from __future__ import annotations

import html
import math
from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import (
    CircuitTopology,
    EarthingSystem,
    ElectricalDesignResult,
    PhaseAssignment,
)
from cyelec.render.i18n import format_branding_line, t
from cyelec.render.text_fit import fit_text_lines, wrap_text_chars


def _meter_tail_spec(result: ElectricalDesignResult) -> str:
    """Return Cyprus EAC compliant meter-tail & main earth conductor spec from supply tier."""
    eac = result.eac_compliance
    cores = 4 if eac.supply_phases == 3 else 2
    main_a = eac.recommended_main_breaker_a
    if eac.ct_metering_required or main_a > 100:
        return f"{cores}x50mm2 XLPE/SWA + 25mm2 Cu E (CT)"
    if main_a > 63:
        return f"{cores}x25mm2 XLPE/PVC + 16mm2 Cu E"
    return f"{cores}x16mm2 XLPE/PVC + 16mm2 Cu E"


SLD_DXF_LAYERS: dict[str, int] = {
    "E-SLD-BUS": 1,     # Red main busbars
    "E-SLD-DEV": 5,     # Blue protective devices / meter / SPD
    "E-SLD-WIRE": 7,    # White/black schematic conductors
    "E-SLD-EARTH": 3,   # Green earthing / PE / TT electrode
    "E-SLD-TEXT": 2,    # Yellow labels & ratings
    "E-SLD-TITLE": 8,   # Grey border & EAC/ETEK title block
}

_PHASE_HEX: dict[PhaseAssignment, str] = {
    PhaseAssignment.L1: "#8B4513",          # BS 7671 Harmonized Brown
    PhaseAssignment.L2: "#1A202C",          # BS 7671 Harmonized Black
    PhaseAssignment.L3: "#718096",          # BS 7671 Harmonized Grey
    PhaseAssignment.THREE_PHASE: "#1B365D",  # Navy for 3P feeders
}


def _esc(val: object) -> str:
    return html.escape(str(val), quote=True)


def render_sld_svg(result: ElectricalDesignResult) -> str:
    """Render deterministic IEC 60617 / BS 7671 A3-landscape vector SVG Single-Line Diagram."""
    eac = result.eac_compliance
    prov = result.provenance
    lang = result.report_language
    branding_line = format_branding_line(result.branding)
    circuits = list(result.circuits)
    if result.sub_mains:
        sm_order = {sm.board_id: i for i, sm in enumerate(result.sub_mains)}
        circuits.sort(key=lambda c: sm_order.get(c.spec.board_id, len(sm_order)))
    n = max(1, len(circuits))
    sub_board_ids = {sm.board_id for sm in result.sub_mains}
    from cyelec.solver.residential_engineering import is_spd_type12_required

    lps_req = is_spd_type12_required(result)

    # Dynamic width comfortably fitting 13+ circuits while maintaining A3 landscape aspect
    col_w = 112
    margin_x = 56
    width = max(1580, margin_x * 2 + n * col_w)
    sub_offset_y = 118 if result.sub_mains else 0
    height = 1045 + sub_offset_y

    bus_x0 = margin_x + 30
    bus_x1 = width - margin_x - 30
    bus_y = 330
    cx_incomer = width // 2

    # Loss-free header: full project title + subtitle, font-fitted then wrapped (never cut)
    hdr_w = float(width - 68)
    title_txt = f"{t('sld_title', lang)} — {result.building.project_name}"
    title_lines, title_fs = fit_text_lines(
        title_txt, hdr_w, 16.0, max_lines=1, min_font_px=11.0, bold=True
    )
    sub_txt = (
        f"Standard: {eac.eac_profile.value} | "
        f"Supply: {eac.recommended_supply_tier} | "
        f"Earthing: {result.earthing_system.value} "
        f"(Ze/Ra={result.ze_or_ra_ohm:.1f} Ω) | "
        f"DB: {result.building.db_anchor.id}"
        f"{' | ' + branding_line if branding_line else ''}"
    )
    sub_lines, sub_fs = fit_text_lines(sub_txt, hdr_w, 11.0, max_lines=1, min_font_px=9.0)
    title_lh = round(title_fs * 1.25, 1)
    sub_lh = round(sub_fs * 1.35, 1)
    hdr_extra = int(math.ceil((len(title_lines) - 1) * title_lh + (len(sub_lines) - 1) * sub_lh))
    hdr_parts: list[str] = []
    hy = 44.0
    for ln in title_lines:
        hdr_parts.append(
            f'  <text x="34" y="{hy:.1f}" class="title" style="font-size:{title_fs}px;">'
            f"{_esc(ln)}</text>"
        )
        hy += title_lh
    hy = 44.0 + (len(title_lines) - 1) * title_lh + 18.0
    for ln in sub_lines:
        hdr_parts.append(
            f'  <text x="34" y="{hy:.1f}" class="sub" style="font-size:{sub_fs}px;">'
            f"{_esc(ln)}</text>"
        )
        hy += sub_lh

    # Full circuit descriptions wrapped inside the 96 px load boxes (no ellipsis)
    desc_wrap: list[tuple[list[str], float]] = [
        fit_text_lines(c.spec.description, 88.0, 8.2, max_lines=3, min_font_px=7.2)
        for c in circuits
    ]
    max_desc_lines = max((len(dl) for dl, _ in desc_wrap), default=1)
    desc_lh = 10.4
    load_box_h = int(math.ceil(14 + max_desc_lines * desc_lh + 3 + 8 + 16 + 8))
    load_bottom = 660 + sub_offset_y - 16 + load_box_h
    height = max(height, load_bottom + 30 + 185)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "__SVG_OPEN__",
        "  <style>",
        (
            '    .cy-e201 text { font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif; '
            "fill: #1a202c; }"
        ),
        "    .cy-e201 .title { font-size: 16px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e201 .sub { font-size: 11px; fill: #4a5568; }",
        "    .cy-e201 .hdr { font-size: 11.5px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e201 .lbl { font-size: 10px; font-weight: 600; }",
        "    .cy-e201 .sm { font-size: 9px; fill: #2d3748; }",
        "    .cy-e201 .xs { font-size: 8.2px; fill: #4a5568; }",
        "    .cy-e201 .pill { font-size: 9.5px; font-weight: 700; fill: #ffffff; }",
        "    .cy-e201 .pass { fill: #155724; font-weight: 700; font-size: 9px; }",
        "    .cy-e201 .fail { fill: #721c24; font-weight: 700; font-size: 9px; }",
        "  </style>",
        "__SHEET_FRAME__",
        *hdr_parts,
    ]
    if hdr_extra > 0:
        parts.append(f'  <g transform="translate(0,{hdr_extra})">')

    # ------------------------------------------------------------------
    # 1. EAC Supply Incomer -> Meter -> Tails -> Isolator -> [100mA Type-S] -> Busbar
    # ------------------------------------------------------------------
    meter_y = 110
    iso_y = 190
    rcd_s_y = 260
    poles_label = "4P" if eac.supply_phases == 3 else "2P"
    supply_wire_label = (
        "3P+N+PE 400/230V 50Hz" if eac.supply_phases == 3 else "1P+N+PE 230V 50Hz"
    )
    tail_spec = _meter_tail_spec(result)
    show_tt_rcd_s = (
        result.earthing_system == EarthingSystem.TT and result.include_tt_main_rcd_s
    )
    spare_ways = max(2, math.ceil(len(circuits) * 0.2))
    has_pv = result.building.pv_capacity_kva > 0.0
    meter_hdr = "EAC BIDIRECTIONAL METER" if has_pv else "EAC kWh METER"
    meter_w = 196 if has_pv else 156
    ct_note = " [CT METERING >100A/ph]" if eac.ct_metering_required else ""
    sub_note = (
        " [SUBSTATION CONSULTATION REQ]"
        if eac.substation_consultation_required
        else (" [SUBSTATION WARNING >=100kVA]" if eac.substation_warning else "")
    )

    parts.extend(
        [
            # EAC service feeder line
            (
                f'  <line x1="{cx_incomer}" y1="68" x2="{cx_incomer}" y2="{meter_y - 22}" '
                'stroke="#1b365d" stroke-width="3" />'
            ),
            (
                f'  <text x="{cx_incomer + 12}" y="79" class="lbl">'
                f"EAC SERVICE CUT-OUT ({_esc(supply_wire_label)})</text>"
            ),
            # kWh / Bidirectional Smart Meter box
            (
                f'  <rect x="{cx_incomer - meter_w // 2}" y="{meter_y - 22}" width="{meter_w}" '
                'height="44" rx="4" fill="#ebf8ff" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <text x="{cx_incomer}" y="{meter_y - 3}" text-anchor="middle" '
                f'class="hdr">{meter_hdr}</text>'
            ),
            (
                f'  <text x="{cx_incomer}" y="{meter_y + 13}" text-anchor="middle" class="sm">'
                f"{_esc(eac.recommended_supply_tier)}"
                f"{' · Smart' if has_pv else ''}</text>"
            ),
            (
                f'  <text x="{cx_incomer + meter_w // 2 + 10}" y="{meter_y + 3}" class="xs">'
                f"{_esc((ct_note + sub_note).strip())}</text>"
            ),
            # Meter Tails -> Main Isolator
            (
                f'  <line x1="{cx_incomer}" y1="{meter_y + 22}" x2="{cx_incomer}" '
                f'y2="{iso_y - 20}" stroke="#1b365d" stroke-width="3" />'
            ),
            (
                f'  <text x="{cx_incomer + 10}" y="{(meter_y + iso_y) // 2 + 3}" class="xs">'
                f"METER TAILS: {_esc(tail_spec)}</text>"
            ),
            # Main Switch-Disconnector (IEC 60617 isolator symbol)
            (
                f'  <rect x="{cx_incomer - 86}" y="{iso_y - 20}" width="172" height="40" '
                'rx="4" fill="#f7fafc" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <line x1="{cx_incomer - 16}" y1="{iso_y + 10}" '
                f'x2="{cx_incomer + 12}" y2="{iso_y - 10}" stroke="#1b365d" stroke-width="2.4" />'
            ),
            (
                f'  <text x="{cx_incomer + 96}" y="{iso_y - 2}" class="lbl">'
                f"MAIN ISOLATOR {eac.recommended_main_breaker_a}A {poles_label}</text>"
            ),
            (
                f'  <text x="{cx_incomer + 96}" y="{iso_y + 12}" class="xs">'
                f"DB: {_esc(result.building.db_anchor.id)} | "
                f"Div. Demand: {eac.total_diversified_kva:.2f} kVA | "
                f"Ipf: {result.prospective_fault_current_ka:.1f} kA</text>"
            ),
            # Main Isolator -> Busbar (passing through optional 100mA Type-S RCD)
            (
                f'  <line x1="{cx_incomer}" y1="{iso_y + 20}" x2="{cx_incomer}" y2="{bus_y}" '
                'stroke="#1b365d" stroke-width="3.2" />'
            ),
        ]
    )

    if show_tt_rcd_s:
        parts.extend(
            [
                (
                    f'  <rect x="{cx_incomer - 92}" y="{rcd_s_y - 19}" width="184" height="38" '
                    'rx="4" fill="#fef3c7" stroke="#b45309" stroke-width="1.7" />'
                ),
                (
                    f'  <text x="{cx_incomer}" y="{rcd_s_y - 2}" text-anchor="middle" '
                    'class="lbl">MAIN RCD 100mA TYPE-S</text>'
                ),
                (
                    f'  <text x="{cx_incomer}" y="{rcd_s_y + 12}" text-anchor="middle" '
                    f'class="xs">{eac.recommended_main_breaker_a}A {poles_label} '
                    "TIME-DELAYED (TT)</text>"
                ),
            ]
        )
    elif result.earthing_system == EarthingSystem.TT:
        parts.append(
            f'  <text x="{cx_incomer + 12}" y="{rcd_s_y}" class="xs">'
            "TT INSULATED TAIL ENTRY — ALL-RCBO NON-COMBUSTIBLE DB</text>"
        )

    # Surge Protection Device (HMY 58.18-1 parameters: Type, Iimp, In, Up, Uc) off incomer
    spd_x = cx_incomer - 245
    # Tap between isolator (bottom iso_y+20) & Type-S RCD (top rcd_s_y-19) so the dashed
    # SPD connection never strikes through the RCD label text.
    spd_y = iso_y + 36
    spd_title = "SPD TYPE 1+2 (12.5kA)" if lps_req else "SPD TYPE 2 (40kA)"
    spd_sub1 = "Iimp 10/350µs · Up≤1.5kV · Uc=275V" if lps_req else "In 20kA · Up≤1.5kV · Uc=275V"
    spd_sub2 = "IEC 62305-2 + HMY 58.18-1" if lps_req else "BS 7671 Reg 443.4 + HMY 58.18-1"
    parts.extend(
        [
            (
                f'  <line x1="{cx_incomer}" y1="{spd_y}" x2="{spd_x + 86}" y2="{spd_y}" '
                'stroke="#4a5568" stroke-width="1.6" stroke-dasharray="5,3" />'
            ),
            (
                f'  <rect x="{spd_x - 86}" y="{spd_y - 22}" width="172" height="44" '
                'rx="3" fill="#fffaf0" stroke="#c05621" stroke-width="1.5" />'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y - 5}" text-anchor="middle" '
                f'class="lbl">{spd_title}</text>'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y + 8}" text-anchor="middle" '
                f'class="xs">{spd_sub1}</text>'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y + 19}" text-anchor="middle" '
                f'class="xs">{spd_sub2}</text>'
            ),
        ]
    )

    # Complete Earthing & Equipotential Bonding Block (MET + 16mm² MEC + 10mm² Bonding)
    earth_x = margin_x + 135
    earth_y = 185
    earth_desc = (
        f"TT EARTH ROD Ø16×1200mm (Ra≤{result.ze_or_ra_ohm:.0f} Ω)"
        if result.earthing_system == EarthingSystem.TT
        else f"{result.earthing_system.value} EARTH (Ze={result.ze_or_ra_ohm:.2f} Ω)"
    )
    parts.extend(
        [
            (
                f'  <rect x="{earth_x - 112}" y="{earth_y - 58}" width="224" height="118" '
                'rx="4" fill="#f0fff4" stroke="#2f855a" stroke-width="1.6" />'
            ),
            (
                f'  <text x="{earth_x}" y="{earth_y - 40}" text-anchor="middle" '
                'class="hdr">EARTHING &amp; BONDING (MET)</text>'
            ),
            (
                f'  <text x="{earth_x}" y="{earth_y - 24}" text-anchor="middle" class="sm">'
                f"{_esc(earth_desc)}</text>"
            ),
            (
                f'  <text x="{earth_x}" y="{earth_y - 10}" text-anchor="middle" class="xs">'
                "16mm² Cu Main Earth Conductor (MEC)</text>"
            ),
            (
                f'  <text x="{earth_x}" y="{earth_y + 4}" text-anchor="middle" class="xs">'
                "10mm² Main Bonding (≤0.5Ω): Water/Gas/Steel</text>"
            ),
            # Classic IEC 60617 earth symbol
            (
                f'  <line x1="{earth_x}" y1="{earth_y + 14}" x2="{earth_x}" y2="{earth_y + 30}" '
                'stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <line x1="{earth_x - 20}" y1="{earth_y + 30}" '
                f'x2="{earth_x + 20}" y2="{earth_y + 30}" stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <line x1="{earth_x - 13}" y1="{earth_y + 37}" '
                f'x2="{earth_x + 13}" y2="{earth_y + 37}" stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <line x1="{earth_x - 6}" y1="{earth_y + 44}" '
                f'x2="{earth_x + 6}" y2="{earth_y + 44}" stroke="#2f855a" stroke-width="2" />'
            ),
        ]
    )

    # Official March 2026 EAC DRG. PVS-S-1.2 Hybrid PV + Storage + R1/R2 Ripple-Control Branch
    if has_pv:
        pv_x = bus_x1 - 175
        pv_kva = result.building.pv_capacity_kva
        pv_ph = "3P" if (eac.supply_phases == 3 and pv_kva > 4.2) else "1P"
        pvs = result.pv_storage_design
        if pvs is not None:
            pv_dc_line = (
                f"{pvs.module_count}×{pvs.module_wp}Wp = {pvs.array_dc_kwp:g}kWp "
                f"({pvs.strings_count}×{pvs.modules_per_string}s) · "
                f"Voc(0°C)={pvs.string_voc_cold_0c_v:.0f}V · {pvs.dc_cable_mm2:g}mm² DC"
            )
            if pvs.battery_enabled:
                eps_tag = " + EPS ATS" if pvs.eps_backup_enabled else ""
                pv_batt_line = (
                    f"BESS {pvs.battery_nominal_kwh:g}kWh LFP ({pvs.battery_bus_voltage_v}V DC, "
                    f"{pvs.battery_max_power_kw:g}kW){eps_tag} · PVS-S-1.2"
                )
            else:
                pv_batt_line = (
                    f"DC SPD {pvs.inverter_max_dc_v}V + DC Load-Break · "
                    "Storage Ready (EAC PVS-S-1.2)"
                )
        else:
            pv_dc_line = "EN 50549-1 NS Prot. · DC SPD &amp; DC Load-Break"
            pv_batt_line = "Battery Storage Ready (EAC PVS-S-1.2 / 2026)"
        parts.extend(
            [
                (
                    f'  <rect x="{pv_x - 154}" y="72" width="308" height="52" rx="4" '
                    'fill="#ecfdf5" stroke="#059669" stroke-width="1.6" />'
                ),
                (
                    f'  <text x="{pv_x}" y="89" text-anchor="middle" class="hdr">'
                    f"HYBRID PV INVERTER {pv_kva:g} kVA ({pv_ph})</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="103" text-anchor="middle" class="xs">'
                    f"{_esc(pv_dc_line)}</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="116" text-anchor="middle" class="xs">'
                    f"{_esc(pv_batt_line)}</text>"
                ),
                (
                    f'  <line x1="{pv_x}" y1="124" x2="{pv_x}" y2="144" '
                    'stroke="#059669" stroke-width="2.2" />'
                ),
                (
                    f'  <rect x="{pv_x - 154}" y="144" width="308" height="46" rx="3" '
                    'fill="#fef3c7" stroke="#d97706" stroke-width="1.4" />'
                ),
                (
                    f'  <text x="{pv_x}" y="161" text-anchor="middle" class="lbl">'
                    "EAC R1 CURTAILMENT + R2 ZERO-EXPORT RX</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="174" text-anchor="middle" class="xs">'
                    "2P RCBO + 2×1.5mm²+E → Sealable Contactor Box</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="185" text-anchor="middle" class="xs">'
                    "CT Direction Sensor on Main Load Feeder</text>"
                ),
                (
                    f'  <line x1="{pv_x}" y1="190" x2="{pv_x}" y2="212" '
                    'stroke="#059669" stroke-width="2.2" />'
                ),
                (
                    f'  <rect x="{pv_x - 154}" y="212" width="308" height="42" rx="3" '
                    'fill="#dbeafe" stroke="#1d4ed8" stroke-width="1.4" />'
                ),
                (
                    f'  <text x="{pv_x}" y="229" text-anchor="middle" class="lbl">'
                    "LOCKABLE AC ISOLATOR + BIDIRECTIONAL RCBO</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="244" text-anchor="middle" class="xs">'
                    "Dedicated Type A/B 30mA RCBO → Busbar (EAC 2026)</text>"
                ),
                (
                    f'  <line x1="{pv_x}" y1="254" x2="{pv_x}" y2="{bus_y}" '
                    'stroke="#059669" stroke-width="2.4" />'
                ),
            ]
        )

    # ------------------------------------------------------------------
    # 2. Harmonized BS 7671 Main Busbar + 20% Spare Ways Badge
    # ------------------------------------------------------------------
    if eac.supply_phases == 3:
        bus_lines = [
            (bus_y - 12, "#8B4513", "L1"),
            (bus_y - 4, "#1A202C", "L2"),
            (bus_y + 4, "#718096", "L3"),
            (bus_y + 12, "#2B6CB0", "N"),
            (bus_y + 20, "#2F855A", "PE"),
        ]
    else:
        bus_lines = [
            (bus_y - 6, "#8B4513", "L1"),
            (bus_y + 4, "#2B6CB0", "N"),
            (bus_y + 14, "#2F855A", "PE"),
        ]
    for by, color, blabel in bus_lines:
        parts.append(
            f'  <line x1="{bus_x0}" y1="{by}" x2="{bus_x1}" y2="{by}" '
            f'stroke="{color}" stroke-width="2.6" />'
        )
        parts.append(
            f'  <text x="{bus_x0 - 24}" y="{by + 3}" class="xs" '
            f'style="fill:{color};">{blabel}</text>'
        )
    parts.extend(
        [
            (
                f'  <rect x="{bus_x1 - 168}" y="{bus_y - 38}" width="168" height="20" rx="3" '
                'fill="#f8fafc" stroke="#64748b" stroke-width="1" stroke-dasharray="4,2" />'
            ),
            (
                f'  <text x="{bus_x1 - 84}" y="{bus_y - 25}" text-anchor="middle" class="xs">'
                f"+{spare_ways} SPARE WAYS (MIN 20%)</text>"
            ),
        ]
    )

    # ------------------------------------------------------------------
    # 2b. Optional Hierarchical Sub-Distribution Boards (MDB -> DB-GF / DB-1F)
    # ------------------------------------------------------------------
    if result.sub_mains:
        span = bus_x1 - bus_x0
        step_x = span / max(1, n)
        sub_y = bus_y + 72
        for sm in result.sub_mains:
            indices = [i for i, c in enumerate(circuits) if c.spec.board_id == sm.board_id]
            if not indices:
                continue
            sx0 = int(bus_x0 + step_x * min(indices) + 10)
            sx1 = int(bus_x0 + step_x * (max(indices) + 1) - 10)
            scx = (sx0 + sx1) // 2
            box_w = min(360, max(220, sx1 - sx0 - 12))
            sm_color = "#155724" if sm.overall_pass else "#721c24"
            sm_bg = "#f0fff4" if sm.overall_pass else "#fff5f5"
            parts.extend(
                [
                    (
                        f'  <line x1="{scx}" y1="{bus_y + 12}" x2="{scx}" y2="{sub_y - 26}" '
                        'stroke="#1b365d" stroke-width="2.4" />'
                    ),
                    (
                        f'  <rect x="{scx - box_w // 2}" y="{sub_y - 26}" width="{box_w}" '
                        f'height="44" rx="4" fill="{sm_bg}" '
                        f'stroke="{sm_color}" stroke-width="1.5" />'
                    ),
                    (
                        f'  <text x="{scx}" y="{sub_y - 9}" text-anchor="middle" class="hdr">'
                        f"{_esc(sm.parent_board_id)} -&gt; {_esc(sm.board_id)} "
                        f"({_esc(sm.floor)}) — {_esc(sm.breaker_in_a)}A"
                        f"{' + 100mA Type S' if sm.rcd_selective_type_s else ''}</text>"
                    ),
                    (
                        f'  <text x="{scx}" y="{sub_y + 8}" text-anchor="middle" class="sm">'
                        f"{sm.cable_mm2:g}/{sm.cpc_mm2:g}mm² Cu, {sm.length_m:.1f}m | "
                        f"Sub ΔV {sm.voltage_drop_pct:.2f}% | "
                        f"Cumul L/P: {sm.cumulative_lighting_vd_pct:.2f}%/"
                        f"{sm.cumulative_power_vd_pct:.2f}%</text>"
                    ),
                    (
                        f'  <line x1="{scx}" y1="{sub_y + 18}" x2="{scx}" y2="{sub_y + 34}" '
                        'stroke="#1b365d" stroke-width="2.2" />'
                    ),
                    (
                        f'  <line x1="{sx0}" y1="{sub_y + 34}" x2="{sx1}" y2="{sub_y + 34}" '
                        'stroke="#1b365d" stroke-width="3.2" />'
                    ),
                ]
            )
        # Optional Direct MDB Feeders header (for circuits connected straight to MDB)
        direct_indices = [
            i for i, c in enumerate(circuits) if c.spec.board_id not in sub_board_ids
        ]
        if direct_indices:
            dx0 = int(bus_x0 + step_x * min(direct_indices) + 10)
            dx1 = int(bus_x0 + step_x * (max(direct_indices) + 1) - 10)
            dcx = (dx0 + dx1) // 2
            d_w = min(280, max(160, dx1 - dx0 - 10))
            parts.extend(
                [
                    (
                        f'  <rect x="{dcx - d_w // 2}" y="{sub_y - 14}" width="{d_w}" height="24" '
                        'rx="4" fill="#edf2f7" stroke="#4a5568" stroke-width="1.2" '
                        'stroke-dasharray="4,2" />'
                    ),
                    (
                        f'  <text x="{dcx}" y="{sub_y + 2}" text-anchor="middle" class="sm">'
                        f"DIRECT {_esc(result.building.db_anchor.id)} OUTGOING FEEDERS</text>"
                    ),
                ]
            )

    # ------------------------------------------------------------------
    # 3. Final Circuit Feeders (MCB/RCBO, Cable Spec, Length, Ib, ΔV, PASS/FAIL)
    # ------------------------------------------------------------------
    span = bus_x1 - bus_x0
    step_x = span / max(1, n)
    dev_y = 435 + sub_offset_y
    cable_y = 540 + sub_offset_y
    load_y = 660 + sub_offset_y
    pill_y = (bus_y + 114) if result.sub_mains else (bus_y + 34)

    for idx, circ in enumerate(circuits):
        s = circ.spec
        cx = int(bus_x0 + step_x * (idx + 0.5))
        p_color = _PHASE_HEX.get(circ.phase, "#1b365d")
        if result.sub_mains and s.board_id in sub_board_ids:
            bus_tap_y = bus_y + 106
        else:
            bus_tap_y = (
                bus_y - 12
                if circ.phase == PhaseAssignment.L1
                else (
                    bus_y - 4
                    if circ.phase == PhaseAssignment.L2
                    else (bus_y + 4 if circ.phase == PhaseAssignment.L3 else bus_y)
                )
            )
            if eac.supply_phases == 1:
                bus_tap_y = bus_y - 6

        topo_tag = "RING" if s.topology == CircuitTopology.RING_FINAL else "RAD"
        rcd_tag = f"{s.rcd_idn_ma}mA" if (s.rcd_protected and s.rcd_idn_ma) else "NO RCD"
        status_cls = "pass" if circ.overall_pass else "fail"
        status_txt = "PASS" if circ.overall_pass else "FAIL"
        badge_bg = "#d4edda" if circ.overall_pass else "#f8d7da"
        desc_lines, desc_fs = desc_wrap[idx]
        kw_y = load_y - 2 + max_desc_lines * desc_lh + 1
        badge_y = kw_y + 8

        parts.extend(
            [
                # Tap dot on busbar
                f'  <circle cx="{cx}" cy="{bus_tap_y}" r="4" fill="{p_color}" />',
                # Vertical drop from busbar to protective device
                (
                    f'  <line x1="{cx}" y1="{bus_tap_y}" x2="{cx}" y2="{dev_y - 26}" '
                    f'stroke="{p_color}" stroke-width="2" />'
                ),
                # Circuit ID + Phase pill
                (
                    f'  <rect x="{cx - 34}" y="{pill_y}" width="68" height="20" '
                    f'rx="3" fill="{p_color}" />'
                ),
                (
                    f'  <text x="{cx}" y="{pill_y + 13}" text-anchor="middle" class="pill">'
                    f"{_esc(s.circuit_id)} ({_esc(circ.phase.value)})</text>"
                ),
                # IEC 60617 MCB/RCBO symbol box
                (
                    f'  <rect x="{cx - 42}" y="{dev_y - 26}" width="84" height="58" '
                    'rx="3" fill="#f8fafc" stroke="#1b365d" stroke-width="1.4" />'
                ),
                (
                    f'  <text x="{cx}" y="{dev_y - 9}" text-anchor="middle" class="lbl">'
                    f"{circ.breaker_in_a}A Type {circ.breaker_curve.value}</text>"
                ),
                (
                    f'  <text x="{cx}" y="{dev_y + 6}" text-anchor="middle" class="xs">'
                    f"{'RCBO ' + rcd_tag if s.rcd_protected else 'MCB (No RCD)'}</text>"
                ),
                (
                    f'  <text x="{cx}" y="{dev_y + 21}" text-anchor="middle" class="xs">'
                    f"{topo_tag} | Ib={s.design_current_ib_a:.1f}A</text>"
                ),
                # Drop wire to cable spec + load box
                (
                    f'  <line x1="{cx}" y1="{dev_y + 32}" x2="{cx}" y2="{load_y - 16}" '
                    f'stroke="{p_color}" stroke-width="1.8" />'
                ),
                # Ring return loop indicator for ring_final circuits
                *(
                    [
                        (
                            f'  <path d="M {cx + 6} {dev_y + 32} L {cx + 14} {dev_y + 32} '
                            f'L {cx + 14} {load_y - 16} L {cx + 6} {load_y - 16}" '
                            f'fill="none" stroke="{p_color}" stroke-width="1.2" '
                            'stroke-dasharray="3,2" />'
                        )
                    ]
                    if s.topology == CircuitTopology.RING_FINAL
                    else []
                ),
                # Cable & BS 7671 data callout box
                (
                    f'  <rect x="{cx - 46}" y="{cable_y - 24}" width="92" height="72" '
                    'rx="3" fill="#ffffff" stroke="#cbd5e0" stroke-width="1" />'
                ),
                (
                    f'  <text x="{cx}" y="{cable_y - 8}" text-anchor="middle" class="sm">'
                    f"{circ.cable_mm2:g}/{circ.cpc_mm2:g} mm² Cu</text>"
                ),
                (
                    f'  <text x="{cx}" y="{cable_y + 6}" text-anchor="middle" class="xs">'
                    f"PVC Meth.{s.installation_method} {s.ambient_temp_c}°C</text>"
                ),
                (
                    f'  <text x="{cx}" y="{cable_y + 20}" text-anchor="middle" class="xs">'
                    f"L={s.length_m:.1f}m | Iz={circ.iz_derated_a:.1f}A</text>"
                ),
                (
                    f'  <text x="{cx}" y="{cable_y + 34}" text-anchor="middle" class="xs">'
                    f"ΔV={circ.voltage_drop_pct:.2f}% (≤{s.vd_limit_pct:.0f}%)</text>"
                ),
                # Load terminal block + PASS/FAIL badge (full wrapped description, no ellipsis)
                (
                    f'  <rect x="{cx - 48}" y="{load_y - 16}" width="96" height="{load_box_h}" '
                    'rx="4" fill="#edf2f7" stroke="#4a5568" stroke-width="1.2" />'
                ),
                *(
                    f'  <text x="{cx}" y="{load_y - 2 + li * desc_lh:.1f}" text-anchor="middle" '
                    f'class="xs" style="font-size:{desc_fs}px;">{_esc(dl)}</text>'
                    for li, dl in enumerate(desc_lines)
                ),
                (
                    f'  <text x="{cx}" y="{kw_y:.1f}" text-anchor="middle" class="xs">'
                    f"{s.diversified_load_w / 1000.0:.2f} kW (div)</text>"
                ),
                (
                    f'  <rect x="{cx - 24}" y="{badge_y:.1f}" width="48" height="16" '
                    f'rx="3" fill="{badge_bg}" />'
                ),
                (
                    f'  <text x="{cx}" y="{badge_y + 11:.1f}" text-anchor="middle" '
                    f'class="{status_cls}">{status_txt}</text>'
                ),
            ]
        )

    # ------------------------------------------------------------------
    # 4. EAC / ETEK Statutory Plan-Approval Title Block (bottom strip)
    # ------------------------------------------------------------------
    tb_w = width - 64
    sign_w = 472
    sign_x = width - sign_w
    imb_txt = (
        f"L1={result.phase_balance.phase_currents_a['L1']:.1f}A / "
        f"L2={result.phase_balance.phase_currents_a['L2']:.1f}A / "
        f"L3={result.phase_balance.phase_currents_a['L3']:.1f}A "
        f"(Imbalance {result.phase_balance.imbalance_pct:.2f}%)"
        if result.phase_balance is not None
        else f"Single-Phase Peak: {eac.max_phase_demand_a:.1f}A"
    )
    blocker_txt = "BLOCKERS PRESENT" if result.has_blockers else "0 BLOCKERS"
    std_summary = ", ".join(f"{k}={v}" for k, v in sorted(prov.standards_versions.items()))

    eng_val = (
        result.branding.engineer_name
        if (result.branding and result.branding.engineer_name)
        else "______________________________"
    )
    etek_val = (
        result.branding.etek_reg_no
        if (result.branding and result.branding.etek_reg_no)
        else "__________________"
    )
    # Loss-free stacked title-block rows: every row is font-fitted then word-wrapped to its
    # pane width, and the block (and sheet) grow instead of clipping any statutory text.
    left_w = float(sign_x - 48 - 16)
    right_w = float(sign_w - 32)
    left_rows: list[tuple[str, str, float, float, bool]] = [
        ("CYPRUS EAC PLAN-APPROVAL SINGLE-LINE DIAGRAM — DESIGN SUMMARY", "hdr", 11.5, 24.0, True),
        (
            f"Project: {result.building.project_name} | "
            f"Type: {result.building.building_type.value} | "
            f"Area: {result.building.total_floor_area_m2:.2f} m²",
            "sm", 9.0, 22.0, False,
        ),
        (
            f"EAC Supply Tier: {eac.recommended_supply_tier} | "
            f"Connected: {eac.total_connected_kva:.2f} kVA | "
            f"Diversified: {eac.total_diversified_kva:.2f} kVA | "
            f"PV: {result.building.pv_capacity_kva:.2f} kVA",
            "sm", 9.0, 20.0, False,
        ),
        (f"Load Balance: {imb_txt}", "sm", 9.0, 20.0, False),
        (
            f"Provenance: cyelec-pipeline v{prov.pipeline_version} | "
            f"SHA-256: {prov.input_sha256[:16]} | "
            f"UTC: {prov.timestamp_utc} | Status: {blocker_txt}",
            "xs", 8.2, 22.0, False,
        ),
        (f"Standards: {std_summary}", "xs", 8.2, 20.0, False),
    ]
    right_rows: list[tuple[str, str, float, float, bool]] = [
        (t("etek_signoff", lang).upper(), "hdr", 11.5, 24.0, True),
        (f"Licensed Engineer: {eng_val}", "sm", 9.0, 28.0, False),
        (f"ETEK Reg. No.: {etek_val}  Date: ____________", "sm", 9.0, 28.0, False),
        ("Signature & Official Stamp:", "sm", 9.0, 34.0, False),
    ]

    def _stack(
        rows: list[tuple[str, str, float, float, bool]], pane_w: float, x: float
    ) -> tuple[list[tuple[float, str]], float]:
        """Return [(y_offset, svg_text_attrs_and_body)] relative to tb_y + used height."""
        out: list[tuple[float, str]] = []
        y = 0.0
        for txt, cls, fs0, gap, bold in rows:
            lines, fs = fit_text_lines(
                txt, pane_w, fs0, max_lines=1, min_font_px=max(7.0, fs0 - 2.0), bold=bold
            )
            y += gap
            lh = round(fs * 1.3, 1)
            for li, ln in enumerate(lines):
                yy = y + li * lh
                out.append(
                    (yy, f'x="{x:.0f}" class="{cls}" style="font-size:{fs}px;">{_esc(ln)}</text>')
                )
            y += (len(lines) - 1) * lh
        return out, y

    left_items, left_used = _stack(left_rows, left_w, 48.0)
    right_items, right_used = _stack(right_rows, right_w, float(sign_x + 16))
    tb_h = int(math.ceil(max(152.0, left_used + 24.0, right_used + 24.0)))
    height = max(height, load_bottom + 30 + tb_h + 33)
    tb_y = height - 33 - tb_h
    parts.extend(
        [
            (
                f'  <rect x="32" y="{tb_y}" width="{tb_w}" height="{tb_h}" '
                'fill="#f8fafc" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <line x1="{sign_x}" y1="{tb_y}" x2="{sign_x}" '
                f'y2="{tb_y + tb_h}" stroke="#1b365d" stroke-width="1.4" />'
            ),
        ]
    )
    for yy, frag in (*left_items, *right_items):
        parts.append(f'  <text y="{tb_y + yy:.1f}" {frag}')
    if hdr_extra > 0:
        parts.append("  </g>")
    parts.append("</svg>")
    svg_h = height + hdr_extra
    parts[1] = (
        '<svg xmlns="http://www.w3.org/2000/svg" class="cy-e201" '
        f'viewBox="0 0 {width} {svg_h}" width="{width}" height="{svg_h}">'
    )
    parts[parts.index("__SHEET_FRAME__")] = (
        f'  <rect x="0" y="0" width="{width}" height="{svg_h}" fill="#ffffff" />\n'
        f'  <rect x="16" y="16" width="{width - 32}" height="{svg_h - 32}" '
        'fill="none" stroke="#1b365d" stroke-width="2.2" />'
    )
    return "\n".join(parts)


def render_sld_dxf(result: ElectricalDesignResult) -> Drawing:
    """Render deterministic R2018 CAD DXF Single-Line Diagram on standardized E-SLD-* layers."""
    doc = new_dxf("R2018", setup=True)
    doc.header["$INSUNITS"] = 4  # Millimeters
    doc.header["$MEASUREMENT"] = 1

    for layer_name, color_idx in SLD_DXF_LAYERS.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, color=color_idx)

    msp = doc.modelspace()
    eac = result.eac_compliance
    circuits = list(result.circuits)
    if result.sub_mains:
        sm_order = {sm.board_id: i for i, sm in enumerate(result.sub_mains)}
        circuits.sort(key=lambda c: sm_order.get(c.spec.board_id, len(sm_order)))
    n = max(1, len(circuits))
    sub_board_ids = {sm.board_id for sm in result.sub_mains}
    from cyelec.solver.residential_engineering import is_spd_type12_required

    lps_req = is_spd_type12_required(result)

    col_w = 2400.0
    margin = 1200.0
    sheet_w = max(32_000.0, 2.0 * margin + n * col_w)
    sub_shift = 2800.0 if result.sub_mains else 0.0
    sheet_h = 21_000.0 + sub_shift

    branding_line = format_branding_line(result.branding)
    # Drawing border & title strip
    msp.add_lwpolyline(
        [(0, 0), (sheet_w, 0), (sheet_w, sheet_h), (0, sheet_h)],
        close=True,
        dxfattribs={"layer": "E-SLD-TITLE"},
    )
    msp.add_line(
        (0, 3200.0), (sheet_w, 3200.0), dxfattribs={"layer": "E-SLD-TITLE"}
    )
    msp.add_text(
        f"SINGLE-LINE DIAGRAM — {result.building.project_name} ({eac.recommended_supply_tier})",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 360.0},
    ).set_placement((600.0, 2350.0))
    msp.add_text(
        (
            f"PROFILE: {eac.eac_profile.value} | EARTHING: {result.earthing_system.value} "
            f"(Ze/Ra={result.ze_or_ra_ohm:.1f} OHM) | "
            f"ETEK SIGN-OFF REQUIRED | SHA256: {result.provenance.input_sha256[:16]}"
        ),
        dxfattribs={"layer": "E-SLD-TEXT", "height": 220.0},
    ).set_placement((600.0, 1450.0))
    if branding_line:
        msp.add_text(
            f"PRACTICE / ETEK: {branding_line}",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 210.0},
        ).set_placement((600.0, 650.0))

    # Main busbar (y = 14_500 + sub_shift mm)
    bus_x0 = margin
    bus_x1 = sheet_w - margin
    bus_y = 14_500.0 + sub_shift
    cx_inc = sheet_w / 2.0

    msp.add_line((bus_x0, bus_y), (bus_x1, bus_y), dxfattribs={"layer": "E-SLD-BUS"})
    msp.add_line(
        (bus_x0, bus_y - 250.0),
        (bus_x1, bus_y - 250.0),
        dxfattribs={"layer": "E-SLD-BUS"},
    )

    # EAC Meter, Meter Tails, Main Isolator, optional 100mA Type-S RCD, SPD & PV branch
    tail_spec = _meter_tail_spec(result)
    show_tt_rcd_s = (
        result.earthing_system == EarthingSystem.TT and result.include_tt_main_rcd_s
    )
    spare_ways = max(2, math.ceil(len(circuits) * 0.2))

    msp.add_line(
        (cx_inc, bus_y + 5300.0), (cx_inc, bus_y + 4250.0), dxfattribs={"layer": "E-SLD-WIRE"}
    )
    msp.add_lwpolyline(
        [
            (cx_inc - 1600.0, bus_y + 3250.0),
            (cx_inc + 1600.0, bus_y + 3250.0),
            (cx_inc + 1600.0, bus_y + 4250.0),
            (cx_inc - 1600.0, bus_y + 4250.0),
        ],
        close=True,
        dxfattribs={"layer": "E-SLD-DEV"},
    )
    msp.add_text(
        f"EAC METER {eac.recommended_supply_tier}",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 210.0},
    ).set_placement((cx_inc - 1450.0, bus_y + 3650.0))
    msp.add_line(
        (cx_inc, bus_y + 3250.0), (cx_inc, bus_y + 2450.0), dxfattribs={"layer": "E-SLD-WIRE"}
    )
    msp.add_text(
        f"TAILS: {tail_spec}",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 175.0},
    ).set_placement((cx_inc + 220.0, bus_y + 2780.0))

    # Main Isolator box
    msp.add_lwpolyline(
        [
            (cx_inc - 1600.0, bus_y + 1550.0),
            (cx_inc + 1600.0, bus_y + 1550.0),
            (cx_inc + 1600.0, bus_y + 2450.0),
            (cx_inc - 1600.0, bus_y + 2450.0),
        ],
        close=True,
        dxfattribs={"layer": "E-SLD-DEV"},
    )
    msp.add_text(
        f"MAIN ISOLATOR {eac.recommended_main_breaker_a}A",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 210.0},
    ).set_placement((cx_inc - 1450.0, bus_y + 1900.0))
    msp.add_line((cx_inc, bus_y + 1550.0), (cx_inc, bus_y), dxfattribs={"layer": "E-SLD-WIRE"})

    # Optional 100mA Type-S Time-Delayed Main RCD on TT installations
    if show_tt_rcd_s:
        msp.add_lwpolyline(
            [
                (cx_inc - 1800.0, bus_y + 380.0),
                (cx_inc + 1800.0, bus_y + 380.0),
                (cx_inc + 1800.0, bus_y + 1200.0),
                (cx_inc - 1800.0, bus_y + 1200.0),
            ],
            close=True,
            dxfattribs={"layer": "E-SLD-DEV"},
        )
        msp.add_text(
            f"MAIN RCD 100mA TYPE-S ({eac.recommended_main_breaker_a}A)",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 190.0},
        ).set_placement((cx_inc - 1650.0, bus_y + 700.0))

    # Surge Protection Device (Type 1+2 if LPS required, else Type 2 40kA) branched off incomer
    spd_x, spd_y = cx_inc - 4200.0, bus_y + 900.0
    msp.add_line((cx_inc, spd_y), (spd_x + 1600.0, spd_y), dxfattribs={"layer": "E-SLD-WIRE"})
    msp.add_lwpolyline(
        [
            (spd_x - 1600.0, spd_y - 480.0),
            (spd_x + 1600.0, spd_y - 480.0),
            (spd_x + 1600.0, spd_y + 480.0),
            (spd_x - 1600.0, spd_y + 480.0),
        ],
        close=True,
        dxfattribs={"layer": "E-SLD-DEV"},
    )
    spd_dxf_l1 = "SPD TYPE 1+2 (12.5kA 10/350)" if lps_req else "SPD TYPE 2 (40kA) + 32A MCB"
    spd_dxf_l2 = "In 20kA Up<=1.5kV Uc=275V HMY 58.18-1"
    msp.add_text(
        spd_dxf_l1,
        dxfattribs={"layer": "E-SLD-TEXT", "height": 170.0},
    ).set_placement((spd_x - 1480.0, spd_y + 60.0))
    msp.add_text(
        spd_dxf_l2,
        dxfattribs={"layer": "E-SLD-TEXT", "height": 150.0},
    ).set_placement((spd_x - 1480.0, spd_y - 260.0))

    # Complete Earthing & Equipotential Bonding Block on left
    ex, ey = margin + 1600.0, bus_y + 2300.0
    msp.add_line((ex, ey + 600.0), (ex, ey), dxfattribs={"layer": "E-SLD-EARTH"})
    msp.add_line((ex - 450.0, ey), (ex + 450.0, ey), dxfattribs={"layer": "E-SLD-EARTH"})
    msp.add_line(
        (ex - 300.0, ey - 200.0), (ex + 300.0, ey - 200.0), dxfattribs={"layer": "E-SLD-EARTH"}
    )
    msp.add_line(
        (ex - 150.0, ey - 400.0), (ex + 150.0, ey - 400.0), dxfattribs={"layer": "E-SLD-EARTH"}
    )
    msp.add_text(
        f"{result.earthing_system.value} MET ({result.ze_or_ra_ohm:.1f} OHM)",
        dxfattribs={"layer": "E-SLD-EARTH", "height": 200.0},
    ).set_placement((ex - 1200.0, ey + 900.0))
    msp.add_text(
        "16mm2 Cu MEC + 10mm2 WATER/GAS BOND",
        dxfattribs={"layer": "E-SLD-EARTH", "height": 165.0},
    ).set_placement((ex - 1200.0, ey + 600.0))

    # Official 2026 EAC PVS-S-1.2 Hybrid PV + Storage + R1/R2 Curtailment Branch on right
    if result.building.pv_capacity_kva > 0.0:
        pv_x = bus_x1 - 2400.0
        pv_kva = result.building.pv_capacity_kva
        pvs_dxf = result.pv_storage_design
        msp.add_lwpolyline(
            [
                (pv_x - 1900.0, bus_y + 3200.0),
                (pv_x + 1900.0, bus_y + 3200.0),
                (pv_x + 1900.0, bus_y + 4300.0),
                (pv_x - 1900.0, bus_y + 4300.0),
            ],
            close=True,
            dxfattribs={"layer": "E-SLD-EARTH"},
        )
        msp.add_text(
            f"HYBRID PV {pv_kva:g} kVA (EN 50549-1 / PVS-S-1.2)",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 150.0},
        ).set_placement((pv_x - 1780.0, bus_y + 3920.0))
        if pvs_dxf is not None:
            msp.add_text(
                (
                    f"{pvs_dxf.module_count}x{pvs_dxf.module_wp}Wp="
                    f"{pvs_dxf.array_dc_kwp:g}kWp ({pvs_dxf.strings_count}x"
                    f"{pvs_dxf.modules_per_string}s) Voc0={pvs_dxf.string_voc_cold_0c_v:.0f}V"
                ),
                dxfattribs={"layer": "E-SLD-TEXT", "height": 125.0},
            ).set_placement((pv_x - 1780.0, bus_y + 3620.0))
            if pvs_dxf.battery_enabled:
                msp.add_text(
                    (
                        f"BESS {pvs_dxf.battery_nominal_kwh:g}kWh LFP "
                        f"({pvs_dxf.battery_bus_voltage_v}V DC, {pvs_dxf.battery_max_power_kw:g}kW)"
                        + (" + EPS ATS" if pvs_dxf.eps_backup_enabled else "")
                    ),
                    dxfattribs={"layer": "E-SLD-TEXT", "height": 125.0},
                ).set_placement((pv_x - 1780.0, bus_y + 3340.0))
        msp.add_line(
            (pv_x, bus_y + 3200.0), (pv_x, bus_y + 2200.0), dxfattribs={"layer": "E-SLD-EARTH"}
        )
        msp.add_lwpolyline(
            [
                (pv_x - 1800.0, bus_y + 1300.0),
                (pv_x + 1800.0, bus_y + 1300.0),
                (pv_x + 1800.0, bus_y + 2200.0),
                (pv_x - 1800.0, bus_y + 2200.0),
            ],
            close=True,
            dxfattribs={"layer": "E-SLD-DEV"},
        )
        msp.add_text(
            "EAC R1/R2 RX + AC ISOLATOR + BIDIR RCBO",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 150.0},
        ).set_placement((pv_x - 1680.0, bus_y + 1680.0))
        msp.add_line((pv_x, bus_y + 1300.0), (pv_x, bus_y), dxfattribs={"layer": "E-SLD-EARTH"})

    # 20% Spare Ways annotation at right end of main busbar
    msp.add_text(
        f"+{spare_ways} SPARE WAYS (20%)",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 185.0},
    ).set_placement((bus_x1 - 2900.0, bus_y + 260.0))

    step_x = (bus_x1 - bus_x0) / max(1, n)
    sub_bus_y = bus_y - 2600.0

    # Optional Hierarchical Sub-Distribution Boards (MDB -> DB-GF / DB-1F, contiguous spans)
    if result.sub_mains:
        sub_box_top = bus_y - 700.0
        sub_box_bot = bus_y - 2000.0
        for sm in result.sub_mains:
            indices = [i for i, c in enumerate(circuits) if c.spec.board_id == sm.board_id]
            if not indices:
                continue
            sx0 = bus_x0 + step_x * min(indices) + 200.0
            sx1 = bus_x0 + step_x * (max(indices) + 1) - 200.0
            scx = (sx0 + sx1) / 2.0
            half_w = min(3200.0, max(2100.0, (sx1 - sx0) * 0.45))
            msp.add_line(
                (scx, bus_y - 250.0), (scx, sub_box_top), dxfattribs={"layer": "E-SLD-WIRE"}
            )
            msp.add_lwpolyline(
                [
                    (scx - half_w, sub_box_bot),
                    (scx + half_w, sub_box_bot),
                    (scx + half_w, sub_box_top),
                    (scx - half_w, sub_box_top),
                ],
                close=True,
                dxfattribs={"layer": "E-SLD-DEV"},
            )
            rcd_s = " + 100mA TYPE S" if sm.rcd_selective_type_s else ""
            msp.add_text(
                f"{sm.parent_board_id}->{sm.board_id} ({sm.floor}) {sm.breaker_in_a}A{rcd_s}",
                dxfattribs={"layer": "E-SLD-TEXT", "height": 190.0},
            ).set_placement((scx - half_w + 180.0, sub_box_top - 480.0))
            msp.add_text(
                (
                    f"{sm.cable_mm2:g}/{sm.cpc_mm2:g}mm2 L={sm.length_m:.0f}m "
                    f"dV={sm.voltage_drop_pct:.2f}% "
                    f"CumL/P={sm.cumulative_lighting_vd_pct:.1f}/{sm.cumulative_power_vd_pct:.1f}%"
                ),
                dxfattribs={"layer": "E-SLD-TEXT", "height": 160.0},
            ).set_placement((scx - half_w + 180.0, sub_box_bot + 260.0))
            msp.add_line(
                (scx, sub_box_bot), (scx, sub_bus_y), dxfattribs={"layer": "E-SLD-WIRE"}
            )
            msp.add_line((sx0, sub_bus_y), (sx1, sub_bus_y), dxfattribs={"layer": "E-SLD-BUS"})

    # Final circuits
    for idx, circ in enumerate(circuits):
        s = circ.spec
        cx = bus_x0 + step_x * (idx + 0.5)
        # Drop wire from sub-board busbar (for sub-board circuits) or main MDB busbar (direct)
        c_tap_y = sub_bus_y if (result.sub_mains and s.board_id in sub_board_ids) else bus_y
        msp.add_line((cx, c_tap_y), (cx, 12_200.0), dxfattribs={"layer": "E-SLD-WIRE"})
        # Breaker rectangle
        msp.add_lwpolyline(
            [
                (cx - 750.0, 10_800.0),
                (cx + 750.0, 10_800.0),
                (cx + 750.0, 12_200.0),
                (cx - 750.0, 12_200.0),
            ],
            close=True,
            dxfattribs={"layer": "E-SLD-DEV"},
        )
        msp.add_text(
            f"{s.circuit_id} ({circ.phase.value})",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 220.0},
        ).set_placement((cx - 620.0, 11_750.0))
        msp.add_text(
            f"{circ.breaker_in_a}A {circ.breaker_curve.value}",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 190.0},
        ).set_placement((cx - 620.0, 11_200.0))
        # Feeder wire to load
        msp.add_line((cx, 10_800.0), (cx, 6_200.0), dxfattribs={"layer": "E-SLD-WIRE"})
        # Cable & status annotations
        msp.add_text(
            f"{circ.cable_mm2:g}/{circ.cpc_mm2:g}mm2 L={s.length_m:.0f}m",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 165.0},
        ).set_placement((cx - 820.0, 8_600.0))
        msp.add_text(
            f"Ib={s.design_current_ib_a:.1f}A dV={circ.voltage_drop_pct:.1f}%",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 165.0},
        ).set_placement((cx - 820.0, 7_900.0))
        # Full circuit description, word-wrapped onto stacked TEXT lines (never truncated)
        for li, d_ln in enumerate(wrap_text_chars(s.description, 16)):
            msp.add_text(
                d_ln,
                dxfattribs={"layer": "E-SLD-TEXT", "height": 165.0},
            ).set_placement((cx - 820.0, 5_500.0 - li * 250.0))

    _add_sld_paper_space_sheet(doc, result, sheet_w=sheet_w, sheet_h=sheet_h)
    return doc


def _add_sld_paper_space_sheet(
    doc: Drawing,
    result: ElectricalDesignResult,
    *,
    sheet_w: float,
    sheet_h: float,
) -> None:
    """Create a print-ready ISO A3 or A1 Paper-Space layout tab for single_line_diagram.dxf."""
    use_a1 = len(result.circuits) > 24 or bool(result.sub_mains)
    paper_w, paper_h = (841.0, 594.0) if use_a1 else (420.0, 297.0)
    tab_name = "EAC-SLD-A1" if use_a1 else "EAC-SLD-A3"

    if tab_name in doc.layouts:
        doc.layouts.delete(tab_name)
    psp = doc.layouts.new(tab_name)
    psp.page_setup(size=(paper_w, paper_h), margins=(10.0, 10.0, 10.0, 10.0), units="mm")

    m = 10.0
    ix0, iy0, ix1, iy1 = m, m, paper_w - m, paper_h - m
    psp.add_lwpolyline(
        [(0.0, 0.0), (paper_w, 0.0), (paper_w, paper_h), (0.0, paper_h)],
        close=True,
        dxfattribs={"layer": "E-SLD-TITLE"},
    )
    psp.add_lwpolyline(
        [(ix0, iy0), (ix1, iy0), (ix1, iy1), (ix0, iy1)],
        close=True,
        dxfattribs={"layer": "E-SLD-TITLE"},
    )

    eac = result.eac_compliance
    br = result.branding
    firm = br.firm_name if br and br.firm_name else "ETEK LICENSED PRACTICE"
    eng = br.engineer_name if br and br.engineer_name else "ETEK REVIEW PENDING"
    reg = br.etek_reg_no if br and br.etek_reg_no else "ETEK-E"
    # Full-length title-block rows word-wrapped to the paper width; strip grows to fit.
    avail_mm = (ix1 - ix0) - 10.0
    tb_rows: list[tuple[str, float]] = [
        (f"SINGLE-LINE DIAGRAM ({tab_name}) — {result.building.project_name}", 3.4),
        (
            f"SUPPLY: {eac.recommended_supply_tier} | "
            f"EARTH: {result.earthing_system.value} ({result.ze_or_ra_ohm:.1f} OHM) | "
            f"FIRM: {firm} | ENG: {eng} ({reg}) | DWG E-201",
            2.4,
        ),
        (f"ETEK SIGN-OFF REQUIRED | SHA256: {result.provenance.input_sha256[:20]}", 2.1),
    ]
    tb_lines: list[tuple[str, float]] = [
        (ln, h)
        for txt, h in tb_rows
        for ln in wrap_text_chars(txt, max(20, int(avail_mm / (h * 0.62))))
    ]
    tb_h = max(26.0, 4.0 + sum(h * 1.75 for _, h in tb_lines) + 2.0)
    psp.add_line((ix0, iy0 + tb_h), (ix1, iy0 + tb_h), dxfattribs={"layer": "E-SLD-TITLE"})
    ty_mm = iy0 + tb_h - 2.0
    for ln, h in tb_lines:
        ty_mm -= h * 1.75  # top-down baselines with 0.75*h leading
        psp.add_text(ln, dxfattribs={"layer": "E-SLD-TEXT", "height": h}).set_placement(
            (ix0 + 5.0, ty_mm)
        )

    vp_w = (ix1 - ix0) - 8.0
    vp_h = (iy1 - (iy0 + tb_h)) - 8.0
    vp_cx = (ix0 + ix1) / 2.0
    vp_cy = (iy0 + tb_h) + 4.0 + (vp_h / 2.0)
    # Fit full SLD width & height comfortably inside the paper-space viewport
    vh_for_h = sheet_h * 1.06
    vh_for_w = (sheet_w * 1.06) * (vp_h / max(1.0, vp_w))
    psp.add_viewport(
        center=(vp_cx, vp_cy),
        size=(vp_w, vp_h),
        view_center_point=(sheet_w / 2.0, sheet_h / 2.0),
        view_height=max(vh_for_h, vh_for_w),
        dxfattribs={"layer": "E-SLD-TITLE"},
    )


def export_sld_deliverables(
    result: ElectricalDesignResult,
    out_svg: Path,
    out_dxf: Path,
) -> tuple[Path, Path]:
    """Write deterministic Single-Line Diagram SVG and R2018 DXF deliverables."""
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_dxf.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text(render_sld_svg(result), encoding="utf-8")
    doc = render_sld_dxf(result)
    doc.saveas(str(out_dxf))
    return out_svg, out_dxf
