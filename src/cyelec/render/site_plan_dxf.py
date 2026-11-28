"""E-104 External Site Works, Underground Trenching, Earthing, LPS & PV Roof Plan (DXF + SVG)."""

from __future__ import annotations

from html import escape as esc
from pathlib import Path
from typing import TypedDict

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import LightningRiskAssessment
from cyelec.models.spatial import DeviceType, Phase2DesignResult
from cyelec.render.text_fit import (
    dxf_char_budget,
    fit_text_lines,
    wrap_text_chars,
    wrap_text_to_width,
)


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
    title_lines = wrap_text_chars(
        f"E-104: SITE WORKS, UNDERGROUND TRENCHING, EARTHING & LPS — {b.project_name}",
        dxf_char_budget(384.0, 4.0),
    )
    title_extra = (len(title_lines) - 1) * 5.6
    h_tot += title_extra
    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    for i, t_line in enumerate(title_lines):
        msp.add_text(t_line, dxfattribs={"layer": "E-TITLE", "height": 4.0}).set_placement(
            (8.0, h_tot - 10.0 - i * 5.6)
        )
    h_tot -= title_extra  # body coordinates sit below the (possibly multi-line) title
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
    panel_chars = dxf_char_budget(142.0, 2.1)
    for line in spec_lines:
        for k, seg in enumerate(wrap_text_chars(line, panel_chars)):
            msp.add_text(
                seg if k == 0 else f"   {seg}", dxfattribs={"layer": "E-TITLE", "height": 2.1}
            ).set_placement((rx, y_cur))
            y_cur -= 3.6
        y_cur -= 3.6

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
            f"Verdict: {lps.recommended_lps_class}",
            f"SPD: {lps.spd_requirement}",
        ]
    else:
        lps_lines = ["IEC 62305-2 Risk Check: Standard Type 2 SPD at Origin"]
    for line in lps_lines:
        for k, seg in enumerate(wrap_text_chars(line, panel_chars)):
            msp.add_text(
                seg if k == 0 else f"   {seg}", dxfattribs={"layer": "E-TITLE", "height": 2.1}
            ).set_placement((rx, y_cur))
            y_cur -= 3.6
        y_cur -= 3.6

    # ISO A3 Paper-Space tab
    psp = doc.layouts.new("E104-SITE-A3")
    psp.page_setup(size=(420.0, 297.0), margins=(10.0, 10.0, 10.0, 10.0), units="mm")
    psp.add_viewport(
        center=(210.0, 148.5),
        size=(396.0, 273.0),
        view_center_point=(w_tot * 0.5, (h_tot + title_extra) * 0.5),
        view_height=h_tot + title_extra + 6.0,
        dxfattribs={"layer": "E-TITLE"},
    )
    return doc


def _wrap_svg_bullet(
    text: str, max_w: float, font_px: float, *, bold: bool = False
) -> list[str]:
    """Wrap a bullet to `max_w` px (real glyph metrics); continuation lines are indented."""
    first = wrap_text_to_width(text, max_w, font_px, bold=bold)
    if len(first) == 1:
        return first
    head = first[0]
    rest = wrap_text_to_width(text[len(head) :].strip(), max_w - 12.0, font_px, bold=bold)
    return [head, *(f"  {ln}" for ln in rest)]


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

    panel_w = 1072.0 - 700.0
    verdict_lines = _wrap_svg_bullet(f"• Verdict: {lps_verdict}", panel_w, 9.5, bold=True)
    spd_lines = _wrap_svg_bullet(f"• Surge Protection: {lps_spd}", panel_w, 9.3)

    # Full-length (wrapped) title band
    title_lines = wrap_text_to_width(
        f"E-104 SITE WORKS, TRENCHING, EARTHING & LPS — {b.project_name}", 1064.0, 15.0, bold=True
    )
    sub_lines = wrap_text_to_width(
        f"Plot: {info['plot_w']}m x {info['plot_l']}m (Setback {info['setback']}m) | "
        f"{d.eac_compliance.recommended_supply_tier} | "
        f"Earthing: {d.earthing_system.value} ({info['earthing_type']})",
        1064.0,
        10.5,
    )
    hdr_extra = (len(title_lines) - 1) * 18.0 + (len(sub_lines) - 1) * 13.0

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg" class="cy-e104" '
            'viewBox="0 0 1120 __SVG_H__" width="1120" height="__SVG_H__">'
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
        '  <rect x="0" y="0" width="1120" height="__SVG_H__" fill="#ffffff" />',
        (
            '  <rect x="14" y="14" width="1092" height="__FRAME_H__" '
            'fill="none" stroke="#1b365d" stroke-width="2" />'
        ),
        *[
            f'  <text x="28" y="{40 + i * 18}" class="hdr">{esc(t_line)}</text>'
            for i, t_line in enumerate(title_lines)
        ],
        *[
            f'  <text x="28" y="{57 + (len(title_lines) - 1) * 18 + i * 13}" class="sub">'
            f"{esc(s_line)}</text>"
            for i, s_line in enumerate(sub_lines)
        ],
        f'  <g transform="translate(0,{hdr_extra:.0f})">' if hdr_extra else "",
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
        *[
            f'  <text x="242" y="{500 + k * 12}" class="lbl" style="fill:#c53030;">'
            f"{esc(ln)}</text>"
            for k, ln in enumerate(
                wrap_text_to_width(
                    f"2xØ110mm HDPE EAC Duct ({info['trench_m']}m, -750mm)", 120.0, 9.5, bold=True
                )
            )
        ],
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
        pv_lines, pv_fs = fit_text_lines(
            f"ROOF PV PROVISION: {info['pv_kva']:g} kVA (AC+DC RISER + SPD)",
            266.0,
            9.5,
            max_lines=1,
            min_font_px=8.0,
            bold=True,
        )
        pv_y0 = 254.0 - (len(pv_lines) - 1) * 6.0
        for k, ln in enumerate(pv_lines):
            parts.append(
                f'  <text x="340" y="{pv_y0 + k * 12:.1f}" text-anchor="middle" class="lbl" '
                f'style="fill:#975a16;font-size:{pv_fs:.1f}px;">{esc(ln)}</text>'
            )

    # Garden bollards
    for bx, by in ((72, 130), (72, 540), (630, 130), (630, 540)):
        parts.append(
            f'  <circle cx="{bx}" cy="{by}" r="7" '
            'fill="#fefcbf" stroke="#d69e2e" stroke-width="2" />'
        )

    # Right Technical Schedule Panel (every bullet wrapped inside x=700..1072; panel grows)
    ng_v = d.residential_brief.lightning_flash_density_ng
    hm_v = d.residential_brief.building_height_m
    panel_idx = len(parts)
    parts.append("")  # placeholder for the panel background (height known at the end)
    ly_cur = 120.0
    parts.append(
        f'  <text x="700" y="{ly_cur:.0f}" class="sec">A. UNDERGROUND DUCTS &amp; SITE WORKS</text>'
    )
    ly_cur += 4.0

    def bullets(items: list[str], cls: str = "txt", style: str = "") -> None:
        nonlocal ly_cur
        st = f' style="{style}"' if style else ""
        for item in items:
            ly_cur += 19.0
            for k, ln in enumerate(
                _wrap_svg_bullet(item, panel_w, 9.5 if cls == "lbl" else 9.3, bold=cls == "lbl")
            ):
                if k:
                    ly_cur += 14.0
                parts.append(
                    f'  <text x="700" y="{ly_cur:.0f}" class="{cls}"{st}>{esc(ln)}</text>'
                )

    bullets(
        [
            f"• EAC Incoming Duct: 2xØ110mm HDPE ({info['trench_m']}m, -750mm)",
            f"• Telecom / Cyta Duct: 2xØ32mm HDPE + draw pit ({info['trench_m']}m)",
            f"• Earthing Electrode: {d.earthing_system.value} — {eth_title}",
            "• Main Bonding: 10mm² Cu to water, gas & solar metalwork",
            f"• External Lighting: {info['n_bollards']}x IP65 Bollards (Photocell/Timer)",
            f"• Exterior Security: {info['n_cams']}x IP67 PoE CCTV Cameras (Cat6)",
        ]
    )
    ly_cur += 18.0
    parts.append(
        f'  <line x1="700" y1="{ly_cur:.0f}" x2="1072" y2="{ly_cur:.0f}" '
        'stroke="#cbd5e0" stroke-width="1" />'
    )
    ly_cur += 24.0
    parts.append(
        f'  <text x="700" y="{ly_cur:.0f}" class="sec">'
        "B. IEC 62305-2 LIGHTNING RISK ASSESSMENT</text>"
    )
    ly_cur += 4.0
    bullets(
        [
            f"• Cyprus Flash Density Ng: {ng_v:.2f} flashes/km²/yr",
            f"• Equivalent Collection Area Ad: {lps_ad} (H = {hm_v:.1f} m)",
            f"• Calculated Risk R1: {lps_r1} (Tolerable RT = 1.00e-05)",
        ]
    )
    ly_cur += 22.0
    for k, vl in enumerate(verdict_lines):
        if k:
            ly_cur += 16.0
        parts.append(
            f'  <text x="700" y="{ly_cur:.0f}" class="lbl" style="fill:{lps_color};">'
            f"{esc(vl)}</text>"
        )
    ly_cur += 18.0
    for k, sl in enumerate(spd_lines):
        if k:
            ly_cur += 15.0
        parts.append(f'  <text x="700" y="{ly_cur:.0f}" class="txt">{esc(sl)}</text>')
    div_y = int(max(ly_cur + 23, 434))
    sec_c_y = div_y + 24
    tr_y = sec_c_y + 18
    panel_h = max(620.0, tr_y + 190 + 14 - 94.0)
    parts[panel_idx] = (
        f'  <rect x="684" y="94" width="404" height="{panel_h:.0f}" rx="6" '
        'fill="#f8fafc" stroke="#cbd5e0" stroke-width="1.5" />'
    )
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
            "  </g>" if hdr_extra else "",
            "</svg>",
        ]
    )
    body_h = max(755.0, 94.0 + panel_h + 41.0)
    svg_h = int(body_h + hdr_extra)
    return (
        "\n".join(p for p in parts if p)
        .replace("__SVG_H__", str(svg_h))
        .replace("__FRAME_H__", str(svg_h - 28))
    )



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
