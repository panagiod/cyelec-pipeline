"""E-104 External Site Works, Underground Trenching, Earthing, LPS & PV Roof Plan (DXF + SVG)."""

from __future__ import annotations

from html import escape as esc
from pathlib import Path
from typing import TypedDict

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import LightningRiskAssessment
from cyelec.models.spatial import DeviceType, Phase2DesignResult


class _SiteSummary(TypedDict):
    plot_w: float
    plot_l: float
    setback: float
    trench_m: float
    earthing_type: str
    has_pv: bool
    pv_kva: float
    has_ev: bool
    ev_kw: float
    has_pool: bool
    has_gate: bool
    n_bollards: int
    n_cams: int
    lps: LightningRiskAssessment | None


def _site_geometry_summary(result: Phase2DesignResult) -> _SiteSummary:
    d = result.design
    b = d.building
    brief = d.residential_brief
    sp = result.spatial

    has_pv = b.pv_capacity_kva > 0.0
    effective_ev = brief.ev_charger_kw if brief.ev_charger_kw is not None else b.ev_charger_kw
    has_ev = effective_ev > 0.0
    has_pool = brief.include_pool
    has_gate = any(x.device_type == DeviceType.GATE_MOTOR for x in sp.devices)
    n_bollards = sum(1 for x in sp.devices if x.device_type == DeviceType.GARDEN_BOLLARD)
    n_cams = sum(1 for x in sp.devices if x.device_type == DeviceType.CCTV_CAMERA)
    trench_m = round(max(6.0, brief.street_setback_m + 3.0), 1)
    lps = d.lightning_risk
    return {
        "plot_w": brief.plot_width_m,
        "plot_l": brief.plot_length_m,
        "setback": brief.street_setback_m,
        "trench_m": trench_m,
        "earthing_type": brief.earthing_electrode_type,
        "has_pv": has_pv,
        "pv_kva": b.pv_capacity_kva,
        "has_ev": has_ev,
        "ev_kw": effective_ev,
        "has_pool": has_pool,
        "has_gate": has_gate,
        "n_bollards": n_bollards,
        "n_cams": n_cams,
        "lps": lps,
    }


def render_site_plan_dxf(result: Phase2DesignResult) -> Drawing:
    """Build pristine R2018 DXF E-104 Site Works, Trenching, Earthing & LPS sheet (ISO A3)."""
    d = result.design
    b = d.building
    info = _site_geometry_summary(result)

    doc = new_dxf("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1

    for lname, color in (
        ("E-TITLE", 7),
        ("E-SITE-PLOT", 8),
        ("E-SITE-BLDG", 7),
        ("E-SITE-TRENCH-EAC", 1),
        ("E-SITE-TRENCH-ELV", 5),
        ("E-EARTHING", 3),
        ("E-SITE-LIGHT", 2),
        ("E-SITE-PV", 40),
    ):
        if lname not in doc.layers:
            doc.layers.add(lname, color=color)

    msp = doc.modelspace()
    # 400 x 275 mm clean schematic layout for 1:1 ISO A3 Paper Space
    w_tot, h_tot = 400.0, 275.0
    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text(
        f"E-104: SITE WORKS, UNDERGROUND TRENCHING, EARTHING & LPS — {b.project_name[:42]}",
        dxfattribs={"layer": "E-TITLE", "height": 4.0},
    ).set_placement((8.0, h_tot - 10.0))
    msp.add_line((0.0, h_tot - 14.0), (w_tot, h_tot - 14.0), dxfattribs={"layer": "E-TITLE"})

    # Left Schematic Site Plot Box (x=12..245, y=38..252)
    px0, py0, px1, py1 = 14.0, 42.0, 240.0, 248.0
    msp.add_lwpolyline(
        [(px0, py0), (px1, py0), (px1, py1), (px0, py1)],
        close=True,
        dxfattribs={"layer": "E-SITE-PLOT"},
    )
    msp.add_text(
        f"PLOT BOUNDARY ({info['plot_w']}m x {info['plot_l']}m) — VERIFY ON SITE PLAN",
        dxfattribs={"layer": "E-TITLE", "height": 2.4},
    ).set_placement((px0 + 4.0, py1 + 3.0))

    # Street / Road along bottom
    msp.add_line((px0, 26.0), (px1, 26.0), dxfattribs={"layer": "E-SITE-PLOT"})
    msp.add_text(
        "PUBLIC ROAD / EAC LV NETWORK & CYTA FIBER DUCT CORRIDOR",
        dxfattribs={"layer": "E-TITLE", "height": 2.6},
    ).set_placement((px0 + 36.0, 31.0))

    # Building Footprint Box centered inside plot
    bx0, by0, bx1, by1 = 62.0, 102.0, 192.0, 206.0
    msp.add_lwpolyline(
        [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)],
        close=True,
        dxfattribs={"layer": "E-SITE-BLDG"},
    )
    msp.add_text(
        f"BUILDING FOOTPRINT ({b.total_floor_area_m2:.1f} m2)",
        dxfattribs={"layer": "E-SITE-BLDG", "height": 3.0},
    ).set_placement((bx0 + 22.0, (by0 + by1) * 0.5 + 8.0))

    # Foundation / Ring Earth Electrode Perimeter (offset 6mm outside building)
    if info["earthing_type"] == "foundation_ring":
        msp.add_lwpolyline(
            [
                (bx0 - 6.0, by0 - 6.0),
                (bx1 + 6.0, by0 - 6.0),
                (bx1 + 6.0, by1 + 6.0),
                (bx0 - 6.0, by1 + 6.0),
            ],
            close=True,
            dxfattribs={"layer": "E-EARTHING"},
        )
        msp.add_text(
            "FOUNDATION / RING EARTH ELECTRODE (30x3.5mm TAPE + TEST LINKS)",
            dxfattribs={"layer": "E-EARTHING", "height": 2.2},
        ).set_placement((bx0 - 6.0, by1 + 9.0))

    # MDB & Telecom Cabinet inside building
    mdb_x, mdb_y = bx0 + 22.0, by0 + 18.0
    rack_x, rack_y = bx0 + 58.0, by0 + 18.0
    msp.add_lwpolyline(
        [
            (mdb_x - 8, mdb_y - 5),
            (mdb_x + 8, mdb_y - 5),
            (mdb_x + 8, mdb_y + 5),
            (mdb_x - 8, mdb_y + 5),
        ],
        close=True,
        dxfattribs={"layer": "E-SITE-TRENCH-EAC"},
    )
    msp.add_text(
        b.db_anchor.id, dxfattribs={"layer": "E-SITE-TRENCH-EAC", "height": 2.5}
    ).set_placement((mdb_x - 6.0, mdb_y - 1.2))
    msp.add_lwpolyline(
        [
            (rack_x - 10, rack_y - 5),
            (rack_x + 10, rack_y - 5),
            (rack_x + 10, rack_y + 5),
            (rack_x - 10, rack_y + 5),
        ],
        close=True,
        dxfattribs={"layer": "E-SITE-TRENCH-ELV"},
    )
    msp.add_text("9U RACK", dxfattribs={"layer": "E-SITE-TRENCH-ELV", "height": 2.3}).set_placement(
        (rack_x - 8.5, rack_y - 1.2)
    )

    # Street Boundary EAC Meter Pillar + TT Earth Pit + Underground 2x110mm Trench
    mtr_x, mtr_y = bx0 + 22.0, py0 + 6.0
    msp.add_lwpolyline(
        [
            (mtr_x - 10, mtr_y - 5),
            (mtr_x + 10, mtr_y - 5),
            (mtr_x + 10, mtr_y + 5),
            (mtr_x - 10, mtr_y + 5),
        ],
        close=True,
        dxfattribs={"layer": "E-SITE-TRENCH-EAC"},
    )
    msp.add_text(
        "EAC METER", dxfattribs={"layer": "E-SITE-TRENCH-EAC", "height": 2.2}
    ).set_placement((mtr_x - 9.0, mtr_y - 1.0))
    msp.add_line((mtr_x, mtr_y + 5), (mdb_x, mdb_y - 5), dxfattribs={"layer": "E-SITE-TRENCH-EAC"})
    msp.add_text(
        f"2x110mm HDPE EAC DUCT ({info['trench_m']}m, H=-750mm)",
        dxfattribs={"layer": "E-SITE-TRENCH-EAC", "height": 2.0},
    ).set_placement((mtr_x - 42.0, (mtr_y + mdb_y) * 0.5))

    # Earth Pit next to Meter Pillar
    ep_x, ep_y = mtr_x - 22.0, mtr_y
    msp.add_circle((ep_x, ep_y), radius=4.5, dxfattribs={"layer": "E-EARTHING"})
    msp.add_line((ep_x, ep_y + 4.5), (bx0 - 6.0, by0 - 6.0), dxfattribs={"layer": "E-EARTHING"})
    msp.add_text("TT EARTH PIT", dxfattribs={"layer": "E-EARTHING", "height": 2.0}).set_placement(
        (ep_x - 12.0, ep_y - 8.0)
    )

    # Telecom Street Pit + 2x32mm Fiber Duct to 9U Rack
    tel_x, tel_y = rack_x, py0 + 6.0
    msp.add_lwpolyline(
        [
            (tel_x - 8, tel_y - 4),
            (tel_x + 8, tel_y - 4),
            (tel_x + 8, tel_y + 4),
            (tel_x - 8, tel_y + 4),
        ],
        close=True,
        dxfattribs={"layer": "E-SITE-TRENCH-ELV"},
    )
    msp.add_text(
        "CYTA PIT", dxfattribs={"layer": "E-SITE-TRENCH-ELV", "height": 2.0}
    ).set_placement((tel_x - 7.5, tel_y - 1.0))
    msp.add_line(
        (tel_x, tel_y + 4), (rack_x, rack_y - 5), dxfattribs={"layer": "E-SITE-TRENCH-ELV"}
    )
    msp.add_text(
        f"2x32mm FIBER DUCT ({info['trench_m']}m)",
        dxfattribs={"layer": "E-SITE-TRENCH-ELV", "height": 2.0},
    ).set_placement((tel_x + 3.0, (tel_y + rack_y) * 0.5))

    # Optional Gate, EV, Pool, PV Roof callouts
    if info["has_gate"]:
        msp.add_text(
            "SLIDING GATE MOTOR + INTERCOM", dxfattribs={"layer": "E-SITE-LIGHT", "height": 2.1}
        ).set_placement((px1 - 66.0, py0 + 4.0))
    if info["has_ev"]:
        msp.add_text(
            f"EV WALLBOX {info['ev_kw']:g}kW (IP65)",
            dxfattribs={"layer": "E-SITE-TRENCH-EAC", "height": 2.1},
        ).set_placement((bx0 - 44.0, by0 + 36.0))
    if info["has_pool"]:
        msp.add_lwpolyline(
            [(bx1 + 12, by0 + 25), (px1 - 6, by0 + 25), (px1 - 6, by1 - 12), (bx1 + 12, by1 - 12)],
            close=True,
            dxfattribs={"layer": "E-SITE-TRENCH-ELV"},
        )
        msp.add_text(
            "POOL SEC.702", dxfattribs={"layer": "E-SITE-TRENCH-ELV", "height": 2.0}
        ).set_placement((bx1 + 14.0, (by0 + by1) * 0.5))
    if info["has_pv"]:
        msp.add_text(
            f"ROOF PV PROVISION: {info['pv_kva']:g} kVA (AC+DC RISER + TYPE 1+2 SPD)",
            dxfattribs={"layer": "E-SITE-PV", "height": 2.2},
        ).set_placement((bx0 + 6.0, by1 - 14.0))

    # Garden Bollard markers
    for gx, gy in (
        (px0 + 14, py0 + 26),
        (px0 + 14, py1 - 20),
        (px1 - 14, py0 + 26),
        (px1 - 14, py1 - 20),
    ):
        msp.add_circle((gx, gy), radius=2.5, dxfattribs={"layer": "E-SITE-LIGHT"})

    # Right Engineering Schedule & IEC 62305-2 Lightning Risk Panel (x=250..394)
    rx = 252.0
    msp.add_line((248.0, 6.0), (248.0, h_tot - 14.0), dxfattribs={"layer": "E-TITLE"})
    y_cur = h_tot - 24.0
    msp.add_text(
        "A. UNDERGROUND TRENCH & EARTHING SPEC",
        dxfattribs={"layer": "E-SITE-TRENCH-EAC", "height": 2.8},
    ).set_placement((rx, y_cur))
    y_cur -= 8.5
    eth_lbl = info["earthing_type"].replace("_", " ").upper()
    spec_lines = [
        f"1. EAC Supply Duct: 2x110mm HDPE, depth 750mm, {info['trench_m']}m",
        f"2. Telecom / Cyta Duct: 2x32mm HDPE + draw pit, {info['trench_m']}m",
        f"3. Earthing System: {d.earthing_system.value} ({eth_lbl})",
        "4. Earth Electrode Target: Ra <= 80 Ohm (16mm2 Cu MEC)",
        "5. Main Equipotential Bonding: 10mm2 Cu Water/Gas/MET",
        f"6. Garden Lighting: {info['n_bollards']}x IP65 Bollards (Photocell/Timer)",
        f"7. Exterior CCTV: {info['n_cams']}x IP67 PoE Cameras (Cat6 to NVR)",
        f"8. Roof PV: {info['pv_kva']:g} kVA Net-Metering AC+DC Riser"
        if info["has_pv"]
        else "8. Roof PV: Spare 32mm Conduit Provision to Roof",
    ]
    for line in spec_lines:
        msp.add_text(line[:64], dxfattribs={"layer": "E-TITLE", "height": 2.1}).set_placement(
            (rx, y_cur)
        )
        y_cur -= 7.2

    y_cur -= 6.0
    msp.add_line((248.0, y_cur + 4.0), (w_tot, y_cur + 4.0), dxfattribs={"layer": "E-TITLE"})
    msp.add_text(
        "B. IEC 62305-2 LIGHTNING RISK ASSESSMENT",
        dxfattribs={"layer": "E-EARTHING", "height": 2.8},
    ).set_placement((rx, y_cur - 3.0))
    y_cur -= 11.5
    lps = info["lps"]
    if lps is not None:
        h_m = d.residential_brief.building_height_m
        lps_lines = [
            f"Flash Density Ng: {lps.ng_flashes_km2_yr:.2f} flashes/km2/yr (Cyprus)",
            f"Collection Area Ad: {lps.collection_area_ad_m2:.1f} m2 (H={h_m:.1f}m)",
            f"Annual Strikes Nd: {lps.nd_events_per_yr:.6f} /yr",
            f"Calculated Risk R1: {lps.risk_r1:.2e} vs RT={lps.tolerable_rt:.1e}",
            f"Verdict: {lps.recommended_lps_class[:54]}",
            f"SPD: {lps.spd_requirement[:56]}",
        ]
    else:
        lps_lines = ["IEC 62305-2 Risk Check: Standard Type 2 SPD at Origin"]
    for line in lps_lines:
        msp.add_text(line[:64], dxfattribs={"layer": "E-TITLE", "height": 2.1}).set_placement(
            (rx, y_cur)
        )
        y_cur -= 7.2

    # ISO A3 Paper-Space tab
    psp = doc.layouts.new("E104-SITE-A3")
    psp.page_setup(size=(420.0, 297.0), margins=(10.0, 10.0, 10.0, 10.0), units="mm")
    psp.add_viewport(
        center=(210.0, 148.5),
        size=(396.0, 273.0),
        view_center_point=(w_tot * 0.5, h_tot * 0.5),
        view_height=h_tot + 6.0,
        dxfattribs={"layer": "E-TITLE"},
    )
    return doc


def _wrap_svg_bullet(text: str, max_chars: int = 52) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = f"  {w}"
    if cur:
        lines.append(cur)
    return lines


def render_site_plan_svg(result: Phase2DesignResult) -> str:
    """Render E-104 Site Works, Trenching, Earthing & IEC 62305-2 LPS sheet as vector SVG."""
    d = result.design
    b = d.building
    info = _site_geometry_summary(result)
    lps = info["lps"]
    lps_verdict = lps.recommended_lps_class if lps else "External LPS Not Required"
    lps_spd = lps.spd_requirement if lps else "Type 2 SPD (40kA) at MDB"
    lps_r1 = f"{lps.risk_r1:.2e}" if lps else "N/A"
    lps_ad = f"{lps.collection_area_ad_m2:.0f} m²" if lps else "N/A"
    lps_color = "#c53030" if (lps and lps.lps_required) else "#2f855a"
    eth_title = info["earthing_type"].replace("_", " ").title()
    w_s = 'class="wlbl"'
    w_sm = 'class="wsm"'

    verdict_lines = _wrap_svg_bullet(f"• Verdict: {lps_verdict}", max_chars=52)
    spd_lines = _wrap_svg_bullet(f"• Surge Protection: {lps_spd}", max_chars=54)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg" class="cy-e104" '
            'viewBox="0 0 1120 755" width="1120" height="755">'
        ),
        "  <style>",
        '    .cy-e104 text { font-family: "DejaVu Sans", Arial, sans-serif; fill: #1a202c; }',
        "    .cy-e104 .hdr { font-size: 15px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e104 .sub { font-size: 10.5px; fill: #4a5568; }",
        "    .cy-e104 .sec { font-size: 11.5px; font-weight: 700; fill: #1b365d; }",
        "    .cy-e104 .lbl { font-size: 9.5px; font-weight: 600; }",
        "    .cy-e104 .txt { font-size: 9.3px; fill: #2d3748; }",
        "    .cy-e104 .wlbl { font-size: 9px; font-weight: 700; fill: #ffffff; }",
        "    .cy-e104 .wsm { font-size: 8.5px; font-weight: 700; fill: #ffffff; }",
        "  </style>",
        '  <rect x="0" y="0" width="1120" height="755" fill="#ffffff" />',
        (
            '  <rect x="14" y="14" width="1092" height="727" '
            'fill="none" stroke="#1b365d" stroke-width="2" />'
        ),
        (
            '  <text x="28" y="40" class="hdr">'
            f"E-104 SITE WORKS, TRENCHING, EARTHING &amp; LPS — {esc(b.project_name)}</text>"
        ),
        (
            f'  <text x="28" y="57" class="sub">Plot: {info["plot_w"]}m x {info["plot_l"]}m '
            f'(Setback {info["setback"]}m) | {esc(d.eac_compliance.recommended_supply_tier)}'
            f' | Earthing: {esc(d.earthing_system.value)} ({esc(info["earthing_type"])})</text>'
        ),
        '  <line x1="14" y1="68" x2="1106" y2="68" stroke="#cbd5e0" stroke-width="1.5" />',
        (
            '  <rect x="42" y="94" width="620" height="538" fill="#f7fafc" '
            'stroke="#4a5568" stroke-width="2" stroke-dasharray="10,4" />'
        ),
        (
            f'  <text x="52" y="112" class="lbl" style="fill:#4a5568;">'
            f'PLOT BOUNDARY ({info["plot_w"]}m x {info["plot_l"]}m) — VERIFY ON SITE PLAN</text>'
        ),
        (
            '  <rect x="42" y="646" width="620" height="68" '
            'fill="#edf2f7" stroke="#a0aec0" stroke-width="1.5" />'
        ),
        (
            '  <text x="352" y="684" text-anchor="middle" class="sec">'
            "PUBLIC STREET — EAC (AHK) LV NETWORK &amp; CYTA FIBER CORRIDOR</text>"
        ),
        (
            '  <rect x="160" y="210" width="360" height="240" '
            'fill="#ffffff" stroke="#1b365d" stroke-width="2.5" />'
        ),
        (
            '  <text x="340" y="318" text-anchor="middle" class="sec">'
            f"BUILDING ENVELOPE ({b.total_floor_area_m2:.1f} m²)</text>"
        ),
        (
            '  <rect x="146" y="196" width="388" height="268" fill="none" '
            'stroke="#2f855a" stroke-width="2" stroke-dasharray="6,3" />'
        )
        if info["earthing_type"] == "foundation_ring"
        else "",
        (
            '  <text x="148" y="188" class="lbl" style="fill:#2f855a;">'
            "FOUNDATION / RING EARTH ELECTRODE (30x3.5mm Cu/Galv Tape + Test Links)</text>"
        )
        if info["earthing_type"] == "foundation_ring"
        else "",
        '  <rect x="200" y="390" width="68" height="34" rx="3" fill="#c53030" />',
        f'  <text x="234" y="411" text-anchor="middle" {w_s}>{esc(b.db_anchor.id)}</text>',
        '  <rect x="330" y="390" width="84" height="34" rx="3" fill="#2b6cb0" />',
        f'  <text x="372" y="411" text-anchor="middle" {w_s}>9U RACK</text>',
        (
            '  <rect x="194" y="588" width="80" height="34" rx="3" '
            'fill="#c53030" stroke="#742a2a" stroke-width="1.5" />'
        ),
        f'  <text x="234" y="609" text-anchor="middle" {w_s}>EAC METER</text>',
        (
            '  <line x1="234" y1="588" x2="234" y2="424" '
            'stroke="#c53030" stroke-width="4" stroke-dasharray="8,4" />'
        ),
        (
            '  <text x="242" y="520" class="lbl" style="fill:#c53030;">'
            f'2xØ110mm HDPE EAC Duct ({info["trench_m"]}m, -750mm)</text>'
        ),
        '  <circle cx="146" cy="605" r="16" fill="#c6f6d5" stroke="#2f855a" stroke-width="2" />',
        (
            '  <text x="146" y="608" text-anchor="middle" '
            'style="fill:#22543d;font-size:8px;font-weight:700;">EARTH</text>'
        ),
        '  <line x1="146" y1="589" x2="146" y2="464" stroke="#2f855a" stroke-width="2.5" />',
        '  <rect x="338" y="590" width="68" height="30" rx="3" fill="#2b6cb0" />',
        f'  <text x="372" y="609" text-anchor="middle" {w_s}>CYTA PIT</text>',
        (
            '  <line x1="372" y1="590" x2="372" y2="424" '
            'stroke="#2b6cb0" stroke-width="3" stroke-dasharray="6,4" />'
        ),
        (
            '  <text x="380" y="545" class="lbl" style="fill:#2b6cb0;">'
            f'2xØ32mm Fiber Duct ({info["trench_m"]}m)</text>'
        ),
    ]
    if info["has_ev"]:
        parts.append('  <rect x="74" y="330" width="76" height="30" rx="3" fill="#d69e2e" />')
        parts.append(
            f'  <text x="112" y="349" text-anchor="middle" {w_sm}>EV {info["ev_kw"]:g}kW</text>'
        )
    if info["has_pool"]:
        parts.append(
            '  <rect x="546" y="230" width="96" height="150" rx="6" '
            'fill="#ebf8ff" stroke="#3182ce" stroke-width="2" />'
        )
        parts.append(
            '  <text x="594" y="305" text-anchor="middle" class="lbl" style="fill:#2b6cb0;">'
            "POOL SEC.702</text>"
        )
    if info["has_gate"]:
        parts.append('  <rect x="506" y="592" width="136" height="26" rx="3" fill="#805ad5" />')
        parts.append(
            f'  <text x="574" y="609" text-anchor="middle" {w_sm}>GATE MOTOR + INTERCOM</text>'
        )
    if info["has_pv"]:
        parts.append(
            '  <rect x="200" y="230" width="280" height="40" rx="4" '
            'fill="#fefcbf" stroke="#b7791f" stroke-width="1.5" />'
        )
        parts.append(
            '  <text x="340" y="254" text-anchor="middle" class="lbl" style="fill:#975a16;">'
            f'ROOF PV PROVISION: {info["pv_kva"]:g} kVA (AC+DC RISER + SPD)</text>'
        )

    # Garden bollards
    for bx, by in ((72, 130), (72, 540), (630, 130), (630, 540)):
        parts.append(
            f'  <circle cx="{bx}" cy="{by}" r="7" '
            'fill="#fefcbf" stroke="#d69e2e" stroke-width="2" />'
        )

    # Right Technical Schedule Panel (wrapped cleanly inside x=684..1088)
    ng_v = d.residential_brief.lightning_flash_density_ng
    hm_v = d.residential_brief.building_height_m
    parts.extend(
        [
            (
                '  <rect x="684" y="94" width="404" height="620" rx="6" '
                'fill="#f8fafc" stroke="#cbd5e0" stroke-width="1.5" />'
            ),
            '  <text x="700" y="120" class="sec">A. UNDERGROUND DUCTS &amp; SITE WORKS</text>',
            (
                '  <text x="700" y="143" class="txt">'
                f'• EAC Incoming Duct: 2xØ110mm HDPE ({info["trench_m"]}m, -750mm)</text>'
            ),
            (
                '  <text x="700" y="162" class="txt">'
                f'• Telecom / Cyta Duct: 2xØ32mm HDPE + draw pit ({info["trench_m"]}m)</text>'
            ),
            (
                '  <text x="700" y="181" class="txt">'
                f"• Earthing Electrode: {esc(d.earthing_system.value)} — {esc(eth_title)}</text>"
            ),
            (
                '  <text x="700" y="200" class="txt">'
                "• Main Bonding: 10mm² Cu to water, gas &amp; solar metalwork</text>"
            ),
            (
                '  <text x="700" y="219" class="txt">'
                f'• External Lighting: {info["n_bollards"]}x IP65 Bollards (Photocell/Timer)</text>'
            ),
            (
                '  <text x="700" y="238" class="txt">'
                f'• Exterior Security: {info["n_cams"]}x IP67 PoE CCTV Cameras (Cat6)</text>'
            ),
            '  <line x1="700" y1="256" x2="1072" y2="256" stroke="#cbd5e0" stroke-width="1" />',
            '  <text x="700" y="280" class="sec">B. IEC 62305-2 LIGHTNING RISK ASSESSMENT</text>',
            (
                '  <text x="700" y="303" class="txt">'
                f"• Cyprus Flash Density Ng: {ng_v:.2f} flashes/km²/yr</text>"
            ),
            (
                '  <text x="700" y="322" class="txt">'
                f"• Equivalent Collection Area Ad: {esc(lps_ad)} (H = {hm_v:.1f} m)</text>"
            ),
            (
                '  <text x="700" y="341" class="txt">'
                f"• Calculated Risk R1: {esc(lps_r1)} (Tolerable RT = 1.00e-05)</text>"
            ),
        ]
    )
    ly_cur = 363
    for vl in verdict_lines:
        parts.append(
            f'  <text x="700" y="{ly_cur}" class="lbl" style="fill:{lps_color};">{esc(vl)}</text>'
        )
        ly_cur += 16
    ly_cur += 2
    for sl in spd_lines:
        parts.append(f'  <text x="700" y="{ly_cur}" class="txt">{esc(sl)}</text>')
        ly_cur += 15
    div_y = max(ly_cur + 8, 434)
    sec_c_y = div_y + 24
    tr_y = sec_c_y + 18
    parts.extend(
        [
            (
                f'  <line x1="700" y1="{div_y}" x2="1072" y2="{div_y}" '
                'stroke="#cbd5e0" stroke-width="1" />'
            ),
            (
                f'  <text x="700" y="{sec_c_y}" class="sec">'
                "C. TRENCH CROSS-SECTION DETAIL (N.T.S.)</text>"
            ),
            (
                f'  <rect x="720" y="{tr_y}" width="330" height="190" '
                'fill="#ffffff" stroke="#a0aec0" stroke-width="1" />'
            ),
            (
                f'  <line x1="720" y1="{tr_y + 32}" x2="1050" y2="{tr_y + 32}" '
                'stroke="#2f855a" stroke-width="2" />'
            ),
            (
                f'  <text x="730" y="{tr_y + 24}" class="lbl" style="fill:#2f855a;">'
                "FINISHED GROUND LEVEL (FGL 0.00)</text>"
            ),
            (
                f'  <line x1="730" y1="{tr_y + 80}" x2="1040" y2="{tr_y + 80}" '
                'stroke="#d69e2e" stroke-width="3" stroke-dasharray="12,4" />'
            ),
            (
                f'  <text x="742" y="{tr_y + 72}" class="txt">'
                "WARNING TAPE (-350mm): DANGER ELECTRICITY</text>"
            ),
            (
                f'  <circle cx="810" cy="{tr_y + 144}" r="16" '
                'fill="#fed7d7" stroke="#c53030" stroke-width="2" />'
            ),
            (
                f'  <circle cx="852" cy="{tr_y + 144}" r="16" '
                'fill="#fed7d7" stroke="#c53030" stroke-width="2" />'
            ),
            f'  <text x="774" y="{tr_y + 176}" class="txt">2xØ110mm EAC (-750mm)</text>',
            (
                f'  <circle cx="965" cy="{tr_y + 150}" r="10" '
                'fill="#bee3f8" stroke="#2b6cb0" stroke-width="2" />'
            ),
            (
                f'  <circle cx="995" cy="{tr_y + 150}" r="10" '
                'fill="#bee3f8" stroke="#2b6cb0" stroke-width="2" />'
            ),
            f'  <text x="938" y="{tr_y + 176}" class="txt">2xØ32mm Cyta</text>',
            "</svg>",
        ]
    )
    return "\n".join(p for p in parts if p)


def export_site_plan_deliverables(
    result: Phase2DesignResult,
    out_dir: Path,
) -> dict[str, Path]:
    """Write E-104 Site Works, Underground Trenching & Earthing DXF + SVG."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_p = out_dir / "E104_site_earthing_plan.dxf"
    svg_p = out_dir / "E104_site_earthing_plan.svg"
    render_site_plan_dxf(result).saveas(str(dxf_p))
    svg_p.write_text(render_site_plan_svg(result), encoding="utf-8")
    return {"e104_site_dxf": dxf_p, "e104_site_svg": svg_p}
