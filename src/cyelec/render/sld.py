"""Deterministic Single-Line Diagram (SLD) renderer outputting print-ready A3 SVG and R2018 DXF."""

from __future__ import annotations

import html
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
    # 1. EAC Supply Incomer -> Meter -> Main Switch-Disconnector -> Busbar
    # ------------------------------------------------------------------
    meter_y = 110
    iso_y = 205
    poles_label = "4P" if eac.supply_phases == 3 else "2P"
    supply_wire_label = (
        "3P+N+PE 400/230V 50Hz" if eac.supply_phases == 3 else "1P+N+PE 230V 50Hz"
    )
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
                f'  <line x1="{cx_incomer}" y1="74" x2="{cx_incomer}" y2="{meter_y - 24}" '
                'stroke="#1b365d" stroke-width="3" />'
            ),
            (
                f'  <text x="{cx_incomer + 12}" y="86" class="lbl">'
                f"EAC SERVICE CUT-OUT ({_esc(supply_wire_label)})</text>"
            ),
            # kWh Meter box
            (
                f'  <rect x="{cx_incomer - 72}" y="{meter_y - 24}" width="144" height="48" '
                'rx="4" fill="#ebf8ff" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <text x="{cx_incomer}" y="{meter_y - 4}" text-anchor="middle" '
                'class="hdr">EAC kWh METER</text>'
            ),
            (
                f'  <text x="{cx_incomer}" y="{meter_y + 13}" text-anchor="middle" class="sm">'
                f"{_esc(eac.recommended_supply_tier)}</text>"
            ),
            (
                f'  <text x="{cx_incomer + 84}" y="{meter_y + 4}" class="xs">'
                f"{_esc((ct_note + sub_note).strip())}</text>"
            ),
            # Meter -> Main Isolator
            (
                f'  <line x1="{cx_incomer}" y1="{meter_y + 24}" x2="{cx_incomer}" '
                f'y2="{iso_y - 22}" stroke="#1b365d" stroke-width="3" />'
            ),
            # Main Switch-Disconnector (IEC 60617 isolator symbol)
            (
                f'  <rect x="{cx_incomer - 86}" y="{iso_y - 22}" width="172" height="44" '
                'rx="4" fill="#f7fafc" stroke="#1b365d" stroke-width="1.8" />'
            ),
            (
                f'  <line x1="{cx_incomer - 16}" y1="{iso_y + 12}" '
                f'x2="{cx_incomer + 12}" y2="{iso_y - 12}" stroke="#1b365d" stroke-width="2.4" />'
            ),
            (
                f'  <text x="{cx_incomer + 96}" y="{iso_y - 3}" class="lbl">'
                f"MAIN ISOLATOR {eac.recommended_main_breaker_a}A {poles_label}</text>"
            ),
            (
                f'  <text x="{cx_incomer + 96}" y="{iso_y + 12}" class="xs">'
                f"DB: {_esc(result.building.db_anchor.id)} | "
                f"Div. Demand: {eac.total_diversified_kva:.2f} kVA</text>"
            ),
            # Main Isolator -> Busbar
            (
                f'  <line x1="{cx_incomer}" y1="{iso_y + 22}" x2="{cx_incomer}" y2="{bus_y}" '
                'stroke="#1b365d" stroke-width="3.2" />'
            ),
        ]
    )

    # Type 2 Surge Protection Device (SPD) branch off incomer
    spd_x = cx_incomer - 210
    spd_y = iso_y + 52
    parts.extend(
        [
            (
                f'  <line x1="{cx_incomer}" y1="{spd_y}" x2="{spd_x + 48}" y2="{spd_y}" '
                'stroke="#4a5568" stroke-width="1.6" stroke-dasharray="5,3" />'
            ),
            (
                f'  <rect x="{spd_x - 48}" y="{spd_y - 18}" width="96" height="36" '
                'rx="3" fill="#fffaf0" stroke="#c05621" stroke-width="1.5" />'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y - 2}" text-anchor="middle" '
                'class="lbl">SPD TYPE 2</text>'
            ),
            (
                f'  <text x="{spd_x}" y="{spd_y + 11}" text-anchor="middle" '
                'class="xs">BS 7671 Reg 443</text>'
            ),
        ]
    )

    # Earthing Arrangement block (TT Earth Electrode vs TN-S/TN-C-S)
    earth_x = margin_x + 120
    earth_y = 170
    earth_desc = (
        f"TT EARTH ROD (Ra={result.ze_or_ra_ohm:.1f} Ω)"
        if result.earthing_system == EarthingSystem.TT
        else f"{result.earthing_system.value} EARTH (Ze={result.ze_or_ra_ohm:.2f} Ω)"
    )
    parts.extend(
        [
            (
                f'  <rect x="{earth_x - 95}" y="{earth_y - 44}" width="190" height="88" '
                'rx="4" fill="#f0fff4" stroke="#2f855a" stroke-width="1.5" />'
            ),
            (
                f'  <text x="{earth_x}" y="{earth_y - 24}" text-anchor="middle" '
                'class="hdr">EARTHING TERMINAL (MET)</text>'
            ),
            (
                f'  <text x="{earth_x}" y="{earth_y - 8}" text-anchor="middle" class="sm">'
                f"{_esc(earth_desc)}</text>"
            ),
            # Classic IEC 60617 earth symbol
            (
                f'  <line x1="{earth_x}" y1="{earth_y}" x2="{earth_x}" y2="{earth_y + 16}" '
                'stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <line x1="{earth_x - 20}" y1="{earth_y + 16}" '
                f'x2="{earth_x + 20}" y2="{earth_y + 16}" stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <line x1="{earth_x - 13}" y1="{earth_y + 23}" '
                f'x2="{earth_x + 13}" y2="{earth_y + 23}" stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <line x1="{earth_x - 6}" y1="{earth_y + 30}" '
                f'x2="{earth_x + 6}" y2="{earth_y + 30}" stroke="#2f855a" stroke-width="2" />'
            ),
        ]
    )

    # ------------------------------------------------------------------
    # 2. Harmonized BS 7671 Main Busbar (L1 Brown, L2 Black, L3 Grey, N Blue, PE Green)
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

    # EAC Meter & Main Isolator above busbar
    msp.add_line(
        (cx_inc, bus_y + 5300.0), (cx_inc, bus_y + 4000.0), dxfattribs={"layer": "E-SLD-WIRE"}
    )
    msp.add_lwpolyline(
        [
            (cx_inc - 1400.0, bus_y + 2800.0),
            (cx_inc + 1400.0, bus_y + 2800.0),
            (cx_inc + 1400.0, bus_y + 4000.0),
            (cx_inc - 1400.0, bus_y + 4000.0),
        ],
        close=True,
        dxfattribs={"layer": "E-SLD-DEV"},
    )
    msp.add_text(
        f"EAC METER {eac.recommended_supply_tier}",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 220.0},
    ).set_placement((cx_inc - 1250.0, bus_y + 3250.0))
    msp.add_line(
        (cx_inc, bus_y + 2800.0), (cx_inc, bus_y + 1800.0), dxfattribs={"layer": "E-SLD-WIRE"}
    )

    # Main Isolator box
    msp.add_lwpolyline(
        [
            (cx_inc - 1400.0, bus_y + 800.0),
            (cx_inc + 1400.0, bus_y + 800.0),
            (cx_inc + 1400.0, bus_y + 1800.0),
            (cx_inc - 1400.0, bus_y + 1800.0),
        ],
        close=True,
        dxfattribs={"layer": "E-SLD-DEV"},
    )
    msp.add_text(
        f"MAIN ISOLATOR {eac.recommended_main_breaker_a}A",
        dxfattribs={"layer": "E-SLD-TEXT", "height": 220.0},
    ).set_placement((cx_inc - 1250.0, bus_y + 1180.0))
    msp.add_line((cx_inc, bus_y + 800.0), (cx_inc, bus_y), dxfattribs={"layer": "E-SLD-WIRE"})

    # Earth symbol on left
    ex, ey = margin + 1200.0, bus_y + 2300.0
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
    ).set_placement((ex - 900.0, ey + 750.0))

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

    return doc


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
