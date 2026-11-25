"""Deterministic R2018 DXF electrical overlay renderer (wet zones, devices, conduit routes)."""

from __future__ import annotations

from pathlib import Path

from ezdxf.document import Drawing

from cyelec.models.spatial import DeviceType, Phase2DesignResult
from cyelec.synth.sample_plan import render_building_to_dxf

ELECTRICAL_LAYERS: dict[str, int] = {
    "E-WETZONE-Z0": 1,     # Red: Section 701 Zone 0/1
    "E-WETZONE-Z2": 30,    # Orange: Section 701 Zone 2 (600 mm)
    "E-WETZONE-EXCL": 6,   # Magenta: EAC socket exclusion (2.5 m / 3.0 m)
    "E-LITE": 2,           # Yellow: Ceiling luminaires
    "E-SWIT": 3,           # Green: Light switches
    "E-SOCK": 4,           # Cyan: Twin 13A sockets
    "E-AC": 5,             # Blue: Split A/C isolators
    "E-APPL": 210,         # Purple: Dedicated appliances
    "E-COND": 9,           # Light grey: Routed conduit paths
    "E-TITLE": 7,          # White/Black: EAC / ETEK CAD title block & legend
}


def render_electrical_layout_dxf(result: Phase2DesignResult) -> Drawing:
    """Render architectural floorplan plus Phase 2/3 electrical layers and EAC/ETEK title block."""
    doc = render_building_to_dxf(result.design.building)

    for layer_name, color_idx in ELECTRICAL_LAYERS.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, color=color_idx)

    msp = doc.modelspace()
    spatial = result.spatial

    # 1. Section 701 wet-zone polygons
    for wz in spatial.wet_zones:
        for ring in wz.zone_0_polygons_mm:
            msp.add_lwpolyline(
                [(p.x_mm, p.y_mm) for p in ring],
                close=True,
                dxfattribs={"layer": "E-WETZONE-Z0"},
            )
        for ring in wz.zone_2_polygons_mm:
            msp.add_lwpolyline(
                [(p.x_mm, p.y_mm) for p in ring],
                close=True,
                dxfattribs={"layer": "E-WETZONE-Z2"},
            )
        for ring in wz.socket_exclusion_polygons_mm:
            msp.add_lwpolyline(
                [(p.x_mm, p.y_mm) for p in ring],
                close=True,
                dxfattribs={"layer": "E-WETZONE-EXCL"},
            )

    # 2. Routed NetworkX conduit segments
    for rc in spatial.routed_circuits:
        for seg in rc.segments:
            msp.add_line(
                (seg.start_mm.x_mm, seg.start_mm.y_mm),
                (seg.end_mm.x_mm, seg.end_mm.y_mm),
                dxfattribs={"layer": "E-COND"},
            )

    # 3. Placed electrical device symbols + circuit tags
    for dev in spatial.devices:
        x, y = dev.location_mm.x_mm, dev.location_mm.y_mm
        tag = f"{dev.circuit_id or '?'}:{dev.device_id.split('-')[-1]}"

        if dev.device_type == DeviceType.LUMINAIRE:
            layer = "E-LITE"
            msp.add_circle((x, y), radius=120.0, dxfattribs={"layer": layer})
            msp.add_line((x - 85.0, y - 85.0), (x + 85.0, y + 85.0), dxfattribs={"layer": layer})
            msp.add_line((x - 85.0, y + 85.0), (x + 85.0, y - 85.0), dxfattribs={"layer": layer})
        elif dev.device_type == DeviceType.SWITCH:
            layer = "E-SWIT"
            msp.add_circle((x, y), radius=70.0, dxfattribs={"layer": layer})
            msp.add_line((x, y), (x + 110.0, y + 110.0), dxfattribs={"layer": layer})
        elif dev.device_type == DeviceType.SOCKET_TWIN:
            layer = "E-SOCK"
            msp.add_arc(
                center=(x, y),
                radius=90.0,
                start_angle=0.0,
                end_angle=180.0,
                dxfattribs={"layer": layer},
            )
            msp.add_line((x - 90.0, y), (x + 90.0, y), dxfattribs={"layer": layer})
        elif dev.device_type == DeviceType.SPLIT_AC:
            layer = "E-AC"
            rect = [
                (x - 160.0, y - 80.0),
                (x + 160.0, y - 80.0),
                (x + 160.0, y + 80.0),
                (x - 160.0, y + 80.0),
            ]
            msp.add_lwpolyline(rect, close=True, dxfattribs={"layer": layer})
        else:
            layer = "E-APPL"
            rect = [
                (x - 100.0, y - 100.0),
                (x + 100.0, y - 100.0),
                (x + 100.0, y + 100.0),
                (x - 100.0, y + 100.0),
            ]
            msp.add_lwpolyline(rect, close=True, dxfattribs={"layer": layer})

        lbl = msp.add_text(tag, dxfattribs={"layer": layer, "height": 75.0})
        lbl.set_placement((x + 110.0, y + 40.0))

    # 4. EAC / ETEK Plan-Approval Drawing Border, Title Block & Symbol Legend
    all_pts = [p for r in result.design.building.rooms for p in r.polygon_mm]
    minx = min(p.x_mm for p in all_pts) - 1000.0
    maxx = max(p.x_mm for p in all_pts) + 3800.0
    miny = min(p.y_mm for p in all_pts) - 1800.0
    maxy = max(p.y_mm for p in all_pts) + 1000.0

    msp.add_lwpolyline(
        [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    # Right-side legend & EAC/ETEK title panel
    panel_x = maxx - 3200.0
    msp.add_line((panel_x, miny), (panel_x, maxy), dxfattribs={"layer": "E-TITLE"})

    eac = result.design.eac_compliance
    legend_lines = [
        ("EAC / ETEK ELECTRICAL LAYOUT", 200.0),
        (f"PROJECT: {result.design.building.project_name[:28]}", 130.0),
        (f"PROFILE: {eac.eac_profile.value}", 120.0),
        (f"SUPPLY: {eac.recommended_supply_tier}", 120.0),
        (
            f"EARTHING: {result.design.earthing_system.value} "
            f"({result.design.ze_or_ra_ohm:.1f} OHM)",
            120.0,
        ),
        (f"CONDUIT: {spatial.total_conduit_m:.1f} m | DEV: {len(spatial.devices)}", 120.0),
        ("LEGEND:", 150.0),
        ("  E-LITE : CEILING LUMINAIRE", 110.0),
        ("  E-SWIT : LIGHT SWITCH (1.2m)", 110.0),
        ("  E-SOCK : TWIN 13A SOCKET", 110.0),
        ("  E-AC   : SPLIT A/C ISOLATOR", 110.0),
        ("  E-APPL : DEDICATED APPLIANCE", 110.0),
        ("  E-COND : ROUTED PVC CONDUIT", 110.0),
        (f"  E-WETZONE-EXCL : {eac.bathroom_socket_exclusion_m:.1f}m EXCL", 110.0),
        ("ETEK SIGN-OFF REQUIRED", 140.0),
        (f"SHA256: {result.design.provenance.input_sha256[:16]}", 105.0),
    ]
    cur_y = maxy - 450.0
    for text, h in legend_lines:
        msp.add_text(
            text, dxfattribs={"layer": "E-TITLE", "height": h}
        ).set_placement((panel_x + 150.0, cur_y))
        cur_y -= max(320.0, h * 2.4)

    return doc


def export_electrical_layout_dxf(result: Phase2DesignResult, out_path: Path) -> Path:
    """Write annotated Phase 2 electrical overlay R2018 DXF file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = render_electrical_layout_dxf(result)
    doc.saveas(str(out_path))
    return out_path
