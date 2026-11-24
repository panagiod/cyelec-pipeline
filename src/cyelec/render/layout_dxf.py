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
}


def render_electrical_layout_dxf(result: Phase2DesignResult) -> Drawing:
    """Render architectural floorplan plus Phase 2 electrical layers into an R2018 DXF Drawing."""
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

    return doc


def export_electrical_layout_dxf(result: Phase2DesignResult, out_path: Path) -> Path:
    """Write annotated Phase 2 electrical overlay R2018 DXF file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = render_electrical_layout_dxf(result)
    doc.saveas(str(out_path))
    return out_path
