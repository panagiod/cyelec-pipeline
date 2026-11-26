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


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else f"{text[: max_len - 1]}…"


def _split_sld_label(description: str, max_line: int = 16) -> tuple[str, str]:
    """Split verbose circuit descriptions into two readable SLD lines preserving room identity."""
    if " - " in description:
        left, right = description.split(" - ", 1)
        if "/" in right:
            right = right.split("/")[-1].strip()
        return _truncate(left.strip(), max_line), _truncate(right.strip(), max_line)
    if "(" in description and description.endswith(")"):
        left, right = description.rsplit("(", 1)
        right = right.rstrip(")")
        if "/" in right:
            right = right.split("/")[-1].strip()
        return _truncate(left.strip(), max_line), _truncate(right.strip(), max_line)
    return _truncate(description, max_line), ""


def render_sld_svg(result: ElectricalDesignResult) -> str:
    """Render deterministic IEC 60617 / BS 7671 A3-landscape vector SVG Single-Line Diagram."""
    eac = result.eac_compliance
    prov = result.provenance
    lang = result.report_language
    branding_line = format_branding_line(result.branding)
    circuits = result.circuits
    n = max(1, len(circuits))

    # Dynamic width comfortably fitting 13+ circuits while maintaining A3 landscape aspect
    col_w = 112
    margin_x = 56
    width = max(1580, margin_x * 2 + n * col_w)
    sub_offset_y = 115 if result.sub_mains else 0
    height = 1040 + sub_offset_y

    bus_x0 = margin_x + 30
    bus_x1 = width - margin_x - 30
    bus_y = 330
    cx_incomer = width // 2

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}">'
        ),
        "  <style>",
        '    text { font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif; fill: #1a202c; }',
        "    .title { font-size: 16px; font-weight: 700; fill: #1b365d; }",
        "    .sub { font-size: 11px; fill: #4a5568; }",
        "    .hdr { font-size: 12px; font-weight: 700; fill: #1b365d; }",
        "    .lbl { font-size: 10px; font-weight: 600; }",
        "    .sm { font-size: 9px; fill: #2d3748; }",
        "    .xs { font-size: 8.2px; fill: #4a5568; }",
        "    .pass { fill: #155724; font-weight: 700; font-size: 9px; }",
        "    .fail { fill: #721c24; font-weight: 700; font-size: 9px; }",
        "  </style>",
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" />',
        (
            f'  <rect x="16" y="16" width="{width - 32}" height="{height - 32}" '
            'fill="none" stroke="#1b365d" stroke-width="2.2" />'
        ),
        (
            f'  <text x="34" y="44" class="title">'
            f"{_esc(t('sld_title', lang))} — {_esc(result.building.project_name)}</text>"
        ),
        (
            f'  <text x="34" y="62" class="sub">'
            f"Standard: {_esc(eac.eac_profile.value)} | "
            f"Supply: {_esc(eac.recommended_supply_tier)} | "
            f"Earthing: {_esc(result.earthing_system.value)} "
            f"(Ze/Ra={result.ze_or_ra_ohm:.1f} Ω) | "
            f"DB: {_esc(result.building.db_anchor.id)}"
            f"{' | ' + _esc(branding_line) if branding_line else ''}</text>"
        ),
    ]

    # ------------------------------------------------------------------
    # 1. EAC Supply Incomer -> Meter -> Tails -> Isolator -> [100mA Type-S] -> Busbar
    # ------------------------------------------------------------------
    meter_y = 104
    iso_y = 186
    rcd_s_y = 258
    poles_label = "4P" if eac.supply_phases == 3 else "2P"
    supply_wire_label = (
        "3P+N+PE 400/230V 50Hz" if eac.supply_phases == 3 else "1P+N+PE 230V 50Hz"
    )
    tail_spec = _meter_tail_spec(result)
    show_tt_rcd_s = (
        result.earthing_system == EarthingSystem.TT and result.include_tt_main_rcd_s
    )
    spare_ways = max(2, math.ceil(len(circuits) * 0.2))
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
                f'  <line x1="{cx_incomer}" y1="72" x2="{cx_incomer}" y2="{meter_y - 22}" '
                'stroke="#1b365d" stroke-width="3" />'
            ),
            (
                f'  <text x="{cx_incomer + 12}" y="82" class="lbl">'
                f"EAC SERVICE CUT-OUT ({_esc(supply_wire_label)})</text>"
            ),
            # kWh Meter box
            (
                f'  <rect x="{cx_incomer - 76}" y="{meter_y - 22}" width="152" height="44" '
                'rx="4" fill="#ebf8ff" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <text x="{cx_incomer}" y="{meter_y - 3}" text-anchor="middle" '
                'class="hdr">EAC kWh METER</text>'
            ),
            (
                f'  <text x="{cx_incomer}" y="{meter_y + 13}" text-anchor="middle" class="sm">'
                f"{_esc(eac.recommended_supply_tier)}</text>"
            ),
            (
                f'  <text x="{cx_incomer + 86}" y="{meter_y + 3}" class="xs">'
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

    # Type 2 Surge Protection Device (SPD 40kA + 32A back-up MCB) off incomer
    spd_x = cx_incomer - 235
    spd_y = iso_y + 62
    parts.extend(
        [
            (
                f'  <line x1="{cx_incomer}" y1="{spd_y}" x2="{spd_x + 68}" y2="{spd_y}" '
                'stroke="#4a5568" stroke-width="1.6" stroke-dasharray="5,3" />'
            ),
            (
                f'  <rect x="{spd_x - 68}" y="{spd_y - 22}" width="136" height="44" '
                'rx="3" fill="#fffaf0" stroke="#c05621" stroke-width="1.5" />'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y - 5}" text-anchor="middle" '
                'class="lbl">SPD TYPE 2 (40kA)</text>'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y + 8}" text-anchor="middle" '
                'class="xs">32A MCB Back-Up · Up≤1.5kV</text>'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y + 19}" text-anchor="middle" '
                'class="xs">BS 7671 Reg 443.4</text>'
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
                "10mm² Main Bonding: Water / Gas / Steel</text>"
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

    # Optional PV Net-Metering / Net-Billing AC Branch (right of incomer) when PV > 0
    if result.building.pv_capacity_kva > 0.0:
        pv_x = bus_x1 - 150
        pv_kva = result.building.pv_capacity_kva
        pv_ph = "3P" if (eac.supply_phases == 3 and pv_kva > 4.2) else "1P"
        parts.extend(
            [
                (
                    f'  <rect x="{pv_x - 115}" y="90" width="230" height="44" rx="4" '
                    'fill="#ecfdf5" stroke="#059669" stroke-width="1.6" />'
                ),
                (
                    f'  <text x="{pv_x}" y="108" text-anchor="middle" class="hdr">'
                    f"PV INVERTER {pv_kva:g} kVA ({pv_ph})</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="123" text-anchor="middle" class="xs">'
                    "EN 50549-1 Anti-Islanding · EAC Net-Metering</text>"
                ),
                (
                    f'  <line x1="{pv_x}" y1="134" x2="{pv_x}" y2="160" '
                    'stroke="#059669" stroke-width="2.2" />'
                ),
                (
                    f'  <rect x="{pv_x - 102}" y="160" width="204" height="32" rx="3" '
                    'fill="#f0fdf4" stroke="#047857" stroke-width="1.4" />'
                ),
                (
                    f'  <text x="{pv_x}" y="179" text-anchor="middle" class="sm">'
                    "LOCKABLE ROTARY AC ISOLATOR</text>"
                ),
                (
                    f'  <line x1="{pv_x}" y1="192" x2="{pv_x}" y2="216" '
                    'stroke="#059669" stroke-width="2.2" />'
                ),
                (
                    f'  <rect x="{pv_x - 102}" y="216" width="204" height="34" rx="3" '
                    'fill="#dbeafe" stroke="#1d4ed8" stroke-width="1.4" />'
                ),
                (
                    f'  <text x="{pv_x}" y="231" text-anchor="middle" class="lbl">'
                    "EAC PV GENERATION METER</text>"
                ),
                (
                    f'  <text x="{pv_x}" y="244" text-anchor="middle" class="xs">'
                    "Dedicated Type A/B 30mA RCBO → Busbar</text>"
                ),
                (
                    f'  <line x1="{pv_x}" y1="250" x2="{pv_x}" y2="{bus_y}" '
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
            f'  <text x="{bus_x0 - 24}" y="{by + 3}" class="xs" fill="{color}">{blabel}</text>'
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
            sm_color = "#155724" if sm.overall_pass else "#721c24"
            sm_bg = "#f0fff4" if sm.overall_pass else "#fff5f5"
            parts.extend(
                [
                    (
                        f'  <line x1="{scx}" y1="{bus_y + 12}" x2="{scx}" y2="{sub_y - 26}" '
                        'stroke="#1b365d" stroke-width="2.4" />'
                    ),
                    (
                        f'  <rect x="{scx - 165}" y="{sub_y - 26}" width="330" height="44" '
                        f'rx="4" fill="{sm_bg}" stroke="{sm_color}" stroke-width="1.5" />'
                    ),
                    (
                        f'  <text x="{scx}" y="{sub_y - 9}" text-anchor="middle" class="hdr">'
                        f"{_esc(sm.parent_board_id)} -&gt; {_esc(sm.board_id)} "
                        f"(Floor {_esc(sm.floor)}) — {_esc(sm.breaker_in_a)}A"
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

    # ------------------------------------------------------------------
    # 3. Final Circuit Feeders (MCB/RCBO, Cable Spec, Length, Ib, ΔV, PASS/FAIL)
    # ------------------------------------------------------------------
    span = bus_x1 - bus_x0
    step_x = span / max(1, n)
    dev_y = 435 + sub_offset_y
    cable_y = 540 + sub_offset_y
    load_y = 660 + sub_offset_y

    for idx, circ in enumerate(circuits):
        s = circ.spec
        cx = int(bus_x0 + step_x * (idx + 0.5))
        p_color = _PHASE_HEX.get(circ.phase, "#1b365d")
        if result.sub_mains:
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
        desc_l1, desc_l2 = _split_sld_label(s.description, 17)

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
                    f'  <rect x="{cx - 34}" y="{bus_y + 34}" width="68" height="20" '
                    f'rx="3" fill="{p_color}" />'
                ),
                (
                    f'  <text x="{cx}" y="{bus_y + 47}" text-anchor="middle" '
                    f'style="fill:#ffffff;font-size:9.5px;font-weight:700;">'
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
                # Load terminal block + PASS/FAIL badge
                (
                    f'  <rect x="{cx - 48}" y="{load_y - 16}" width="96" height="72" '
                    'rx="4" fill="#edf2f7" stroke="#4a5568" stroke-width="1.2" />'
                ),
                (
                    f'  <text x="{cx}" y="{load_y - 2}" text-anchor="middle" class="xs">'
                    f"{_esc(desc_l1)}</text>"
                ),
                (
                    f'  <text x="{cx}" y="{load_y + 10}" text-anchor="middle" class="xs">'
                    f"{_esc(desc_l2 or f'{s.diversified_load_w / 1000.0:.2f} kW (div)')}</text>"
                ),
                (
                    f'  <text x="{cx}" y="{load_y + 23}" text-anchor="middle" class="xs">'
                    f"{s.diversified_load_w / 1000.0:.2f} kW (div)</text>"
                ),
                (
                    f'  <rect x="{cx - 24}" y="{load_y + 31}" width="48" height="16" '
                    f'rx="3" fill="{badge_bg}" />'
                ),
                (
                    f'  <text x="{cx}" y="{load_y + 42}" text-anchor="middle" '
                    f'class="{status_cls}">{status_txt}</text>'
                ),
            ]
        )

    # ------------------------------------------------------------------
    # 4. EAC / ETEK Statutory Plan-Approval Title Block (bottom strip)
    # ------------------------------------------------------------------
    tb_y = height - 185
    tb_h = 152
    tb_w = width - 64
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
        else "__________________________________"
    )
    etek_val = (
        result.branding.etek_reg_no
        if (result.branding and result.branding.etek_reg_no)
        else "__________________"
    )
    parts.extend(
        [
            (
                f'  <rect x="32" y="{tb_y}" width="{tb_w}" height="{tb_h}" '
                'fill="#f8fafc" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <line x1="{width - 420}" y1="{tb_y}" x2="{width - 420}" '
                f'y2="{tb_y + tb_h}" stroke="#1b365d" stroke-width="1.4" />'
            ),
            (
                f'  <text x="48" y="{tb_y + 24}" class="hdr">'
                "CYPRUS EAC PLAN-APPROVAL SINGLE-LINE DIAGRAM — DESIGN SUMMARY</text>"
            ),
            (
                f'  <text x="48" y="{tb_y + 46}" class="sm">'
                f"Project: {_esc(result.building.project_name)} | "
                f"Type: {_esc(result.building.building_type.value)} | "
                f"Area: {result.building.total_floor_area_m2:.2f} m²</text>"
            ),
            (
                f'  <text x="48" y="{tb_y + 66}" class="sm">'
                f"EAC Supply Tier: {_esc(eac.recommended_supply_tier)} | "
                f"Connected: {eac.total_connected_kva:.2f} kVA | "
                f"Diversified: {eac.total_diversified_kva:.2f} kVA | "
                f"PV: {result.building.pv_capacity_kva:.2f} kVA</text>"
            ),
            (
                f'  <text x="48" y="{tb_y + 86}" class="sm">'
                f"Load Balance: {_esc(imb_txt)}</text>"
            ),
            (
                f'  <text x="48" y="{tb_y + 108}" class="xs">'
                f"Provenance: cyelec-pipeline v{_esc(prov.pipeline_version)} | "
                f"SHA-256: {_esc(prov.input_sha256[:16])} | "
                f"UTC: {_esc(prov.timestamp_utc)} | Status: {blocker_txt}</text>"
            ),
            (
                f'  <text x="48" y="{tb_y + 128}" class="xs">'
                f"Standards: {_esc(std_summary)}</text>"
            ),
            # Right pane: Statutory ETEK Stamp & Sign-Off Box
            (
                f'  <text x="{width - 404}" y="{tb_y + 24}" class="hdr">'
                f"{_esc(t('etek_signoff', lang).upper())}</text>"
            ),
            (
                f'  <text x="{width - 404}" y="{tb_y + 52}" class="sm">'
                f"Licensed Engineer: {_esc(eng_val)}</text>"
            ),
            (
                f'  <text x="{width - 404}" y="{tb_y + 80}" class="sm">'
                f"ETEK Reg. No.: {_esc(etek_val)}  Date: ____________</text>"
            ),
            (
                f'  <text x="{width - 404}" y="{tb_y + 114}" class="sm">'
                "Signature &amp; Official Stamp:</text>"
            ),
            "</svg>",
        ]
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
    circuits = result.circuits
    n = max(1, len(circuits))

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

    # EAC Meter, Meter Tails, Main Isolator, optional 100mA Type-S RCD, SPD T2 & PV branch
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

    # Type 2 SPD (40kA + 32A back-up MCB) branched off main incomer
    spd_x, spd_y = cx_inc - 4200.0, bus_y + 900.0
    msp.add_line((cx_inc, spd_y), (spd_x + 1500.0, spd_y), dxfattribs={"layer": "E-SLD-WIRE"})
    msp.add_lwpolyline(
        [
            (spd_x - 1500.0, spd_y - 480.0),
            (spd_x + 1500.0, spd_y - 480.0),
            (spd_x + 1500.0, spd_y + 480.0),
            (spd_x - 1500.0, spd_y + 480.0),
        ],
        close=True,
        dxfattribs={"layer": "E-SLD-DEV"},
    )
    msp.add_text(
        "SPD TYPE 2 (40kA) + 32A MCB",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 175.0},
    ).set_placement((spd_x - 1380.0, spd_y + 60.0))
    msp.add_text(
        "BS 7671 REG 443.4 (Up<=1.5kV)",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 150.0},
    ).set_placement((spd_x - 1380.0, spd_y - 260.0))

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

    # Optional PV Net-Metering AC Branch on right of incomer
    if result.building.pv_capacity_kva > 0.0:
        pv_x = bus_x1 - 2400.0
        pv_kva = result.building.pv_capacity_kva
        msp.add_lwpolyline(
            [
                (pv_x - 1700.0, bus_y + 3200.0),
                (pv_x + 1700.0, bus_y + 3200.0),
                (pv_x + 1700.0, bus_y + 4200.0),
                (pv_x - 1700.0, bus_y + 4200.0),
            ],
            close=True,
            dxfattribs={"layer": "E-SLD-EARTH"},
        )
        msp.add_text(
            f"PV INVERTER {pv_kva:g} kVA (EN 50549-1)",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 175.0},
        ).set_placement((pv_x - 1550.0, bus_y + 3600.0))
        msp.add_line(
            (pv_x, bus_y + 3200.0), (pv_x, bus_y + 2200.0), dxfattribs={"layer": "E-SLD-EARTH"}
        )
        msp.add_lwpolyline(
            [
                (pv_x - 1700.0, bus_y + 1300.0),
                (pv_x + 1700.0, bus_y + 1300.0),
                (pv_x + 1700.0, bus_y + 2200.0),
                (pv_x - 1700.0, bus_y + 2200.0),
            ],
            close=True,
            dxfattribs={"layer": "E-SLD-DEV"},
        )
        msp.add_text(
            "AC ISOLATOR + PV GEN METER",
            dxfattribs={"layer": "E-SLD-TEXT", "height": 170.0},
        ).set_placement((pv_x - 1550.0, bus_y + 1680.0))
        msp.add_line((pv_x, bus_y + 1300.0), (pv_x, bus_y), dxfattribs={"layer": "E-SLD-EARTH"})

    # 20% Spare Ways annotation at right end of main busbar
    msp.add_text(
        f"+{spare_ways} SPARE WAYS (20%)",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 185.0},
    ).set_placement((bus_x1 - 2900.0, bus_y + 260.0))

    step_x = (bus_x1 - bus_x0) / max(1, n)
    tap_bus_y = bus_y

    # Optional Hierarchical Sub-Distribution Boards (MDB -> DB-GF / DB-1F)
    if result.sub_mains:
        sub_box_top = bus_y - 700.0
        sub_box_bot = bus_y - 2000.0
        sub_bus_y = bus_y - 2600.0
        tap_bus_y = sub_bus_y
        for sm in result.sub_mains:
            indices = [i for i, c in enumerate(circuits) if c.spec.board_id == sm.board_id]
            if not indices:
                continue
            sx0 = bus_x0 + step_x * min(indices) + 200.0
            sx1 = bus_x0 + step_x * (max(indices) + 1) - 200.0
            scx = (sx0 + sx1) / 2.0
            msp.add_line(
                (scx, bus_y - 250.0), (scx, sub_box_top), dxfattribs={"layer": "E-SLD-WIRE"}
            )
            msp.add_lwpolyline(
                [
                    (scx - 3200.0, sub_box_bot),
                    (scx + 3200.0, sub_box_bot),
                    (scx + 3200.0, sub_box_top),
                    (scx - 3200.0, sub_box_top),
                ],
                close=True,
                dxfattribs={"layer": "E-SLD-DEV"},
            )
            rcd_s = " + 100mA TYPE S" if sm.rcd_selective_type_s else ""
            msp.add_text(
                f"{sm.parent_board_id}->{sm.board_id} ({sm.floor}) {sm.breaker_in_a}A{rcd_s}",
                dxfattribs={"layer": "E-SLD-TEXT", "height": 200.0},
            ).set_placement((scx - 3000.0, sub_box_top - 480.0))
            msp.add_text(
                (
                    f"{sm.cable_mm2:g}/{sm.cpc_mm2:g}mm2 L={sm.length_m:.0f}m "
                    f"dV={sm.voltage_drop_pct:.2f}% "
                    f"CumL/P={sm.cumulative_lighting_vd_pct:.1f}/{sm.cumulative_power_vd_pct:.1f}%"
                ),
                dxfattribs={"layer": "E-SLD-TEXT", "height": 170.0},
            ).set_placement((scx - 3000.0, sub_box_bot + 260.0))
            msp.add_line(
                (scx, sub_box_bot), (scx, sub_bus_y), dxfattribs={"layer": "E-SLD-WIRE"}
            )
            msp.add_line((sx0, sub_bus_y), (sx1, sub_bus_y), dxfattribs={"layer": "E-SLD-BUS"})

    # Final circuits
    for idx, circ in enumerate(circuits):
        s = circ.spec
        cx = bus_x0 + step_x * (idx + 0.5)
        # Drop wire from MDB or sub-board busbar
        msp.add_line((cx, tap_bus_y), (cx, 12_200.0), dxfattribs={"layer": "E-SLD-WIRE"})
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
        msp.add_text(
            _truncate(s.description, 16),
            dxfattribs={"layer": "E-SLD-TEXT", "height": 165.0},
        ).set_placement((cx - 820.0, 5_500.0))

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
    use_a1 = len(result.circuits) > 18 or bool(result.sub_mains)
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

    tb_h = 26.0
    psp.add_line((ix0, iy0 + tb_h), (ix1, iy0 + tb_h), dxfattribs={"layer": "E-SLD-TITLE"})
    eac = result.eac_compliance
    br = result.branding
    firm = br.firm_name if br and br.firm_name else "ETEK LICENSED PRACTICE"
    eng = br.engineer_name if br and br.engineer_name else "ETEK REVIEW PENDING"
    reg = br.etek_reg_no if br and br.etek_reg_no else "ETEK-E"
    psp.add_text(
        f"SINGLE-LINE DIAGRAM ({tab_name}) — {result.building.project_name[:38]}",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 3.4},
    ).set_placement((ix0 + 5.0, iy0 + 18.0))
    psp.add_text(
        (
            f"SUPPLY: {eac.recommended_supply_tier} | "
            f"EARTH: {result.earthing_system.value} ({result.ze_or_ra_ohm:.1f} OHM) | "
            f"FIRM: {firm[:22]} | ENG: {eng[:16]} ({reg[:20]}) | DWG E-201"
        ),
        dxfattribs={"layer": "E-SLD-TEXT", "height": 2.4},
    ).set_placement((ix0 + 5.0, iy0 + 9.0))
    psp.add_text(
        f"ETEK SIGN-OFF REQUIRED | SHA256: {result.provenance.input_sha256[:20]}",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 2.1},
    ).set_placement((ix0 + 5.0, iy0 + 3.0))

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
