"""Deterministic R2018 DXF electrical overlay renderer (wet zones, devices, conduit routes)."""

from __future__ import annotations

from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import readfile as read_dxf
from ezdxf.lldxf.const import DXFError

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


def _resolve_original_dxf_path(
    result: Phase2DesignResult,
    source_dxf_path: Path | None = None,
) -> Path | None:
    if source_dxf_path is not None and source_dxf_path.is_file():
        return source_dxf_path if source_dxf_path.suffix.lower() == ".dxf" else None
    src = result.design.building.source_file
    if not src or "," in src:
        return None
    candidate = Path(src)
    if candidate.is_file() and candidate.suffix.lower() == ".dxf":
        return candidate
    return None


def render_electrical_layout_dxf(
    result: Phase2DesignResult,
    *,
    source_dxf_path: Path | None = None,
) -> Drawing:
    """Overlay electrical layers onto the architect's original DXF (or synthetic fallback)."""
    orig_path = _resolve_original_dxf_path(result, source_dxf_path)
    preserved_underlay = False
    inv_scale = 1.0
    if orig_path is not None:
        try:
            doc = read_dxf(str(orig_path))
            preserved_underlay = True
            unit_scale = result.design.building.source_unit_scale_to_mm
            inv_scale = 1.0 / unit_scale if unit_scale > 0 else 1.0
        except (OSError, DXFError):
            doc = render_building_to_dxf(result.design.building)
    else:
        doc = render_building_to_dxf(result.design.building)

    def _s(v_mm: float) -> float:
        return v_mm * inv_scale

    for layer_name, color_idx in ELECTRICAL_LAYERS.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, color=color_idx)

    msp = doc.modelspace()
    spatial = result.spatial

    # Mark DB1 anchor clearly when overlaying onto the architect's original DXF
    if preserved_underlay:
        db_pt = result.design.building.db_anchor.location_mm
        dx, dy = _s(db_pt.x_mm), _s(db_pt.y_mm)
        db_w, db_h = _s(220.0), _s(120.0)
        msp.add_lwpolyline(
            [
                (dx - db_w, dy - db_h),
                (dx + db_w, dy - db_h),
                (dx + db_w, dy + db_h),
                (dx - db_w, dy + db_h),
            ],
            close=True,
            dxfattribs={"layer": "E-TITLE"},
        )
        msp.add_text(
            result.design.building.db_anchor.id,
            dxfattribs={"layer": "E-TITLE", "height": _s(120.0)},
        ).set_placement((dx + _s(250.0), dy - _s(50.0)))

    # 1. Section 701 wet-zone polygons
    for wz in spatial.wet_zones:
        for ring in wz.zone_0_polygons_mm:
            msp.add_lwpolyline(
                [(_s(p.x_mm), _s(p.y_mm)) for p in ring],
                close=True,
                dxfattribs={"layer": "E-WETZONE-Z0"},
            )
        for ring in wz.zone_2_polygons_mm:
            msp.add_lwpolyline(
                [(_s(p.x_mm), _s(p.y_mm)) for p in ring],
                close=True,
                dxfattribs={"layer": "E-WETZONE-Z2"},
            )
        for ring in wz.socket_exclusion_polygons_mm:
            msp.add_lwpolyline(
                [(_s(p.x_mm), _s(p.y_mm)) for p in ring],
                close=True,
                dxfattribs={"layer": "E-WETZONE-EXCL"},
            )

    # 2. Routed NetworkX conduit segments
    for rc in spatial.routed_circuits:
        for seg in rc.segments:
            msp.add_line(
                (_s(seg.start_mm.x_mm), _s(seg.start_mm.y_mm)),
                (_s(seg.end_mm.x_mm), _s(seg.end_mm.y_mm)),
                dxfattribs={"layer": "E-COND"},
            )

    # 3. Placed electrical device symbols + circuit tags
    for dev in spatial.devices:
        x, y = _s(dev.location_mm.x_mm), _s(dev.location_mm.y_mm)
        tag = f"{dev.circuit_id or '?'}:{dev.device_id.split('-')[-1]}"

        if dev.device_type == DeviceType.LUMINAIRE:
            layer = "E-LITE"
            r = _s(120.0)
            d = _s(85.0)
            msp.add_circle((x, y), radius=r, dxfattribs={"layer": layer})
            msp.add_line((x - d, y - d), (x + d, y + d), dxfattribs={"layer": layer})
            msp.add_line((x - d, y + d), (x + d, y - d), dxfattribs={"layer": layer})
        elif dev.device_type == DeviceType.SWITCH:
            layer = "E-SWIT"
            msp.add_circle((x, y), radius=_s(70.0), dxfattribs={"layer": layer})
            msp.add_line((x, y), (x + _s(110.0), y + _s(110.0)), dxfattribs={"layer": layer})
        elif dev.device_type == DeviceType.SOCKET_TWIN:
            layer = "E-SOCK"
            sr = _s(90.0)
            msp.add_arc(
                center=(x, y),
                radius=sr,
                start_angle=0.0,
                end_angle=180.0,
                dxfattribs={"layer": layer},
            )
            msp.add_line((x - sr, y), (x + sr, y), dxfattribs={"layer": layer})
        elif dev.device_type == DeviceType.SPLIT_AC:
            layer = "E-AC"
            rx, ry = _s(160.0), _s(80.0)
            rect = [(x - rx, y - ry), (x + rx, y - ry), (x + rx, y + ry), (x - rx, y + ry)]
            msp.add_lwpolyline(rect, close=True, dxfattribs={"layer": layer})
        else:
            layer = "E-APPL"
            rr = _s(100.0)
            rect = [(x - rr, y - rr), (x + rr, y - rr), (x + rr, y + rr), (x - rr, y + rr)]
            msp.add_lwpolyline(rect, close=True, dxfattribs={"layer": layer})

        lbl = msp.add_text(tag, dxfattribs={"layer": layer, "height": _s(75.0)})
        lbl.set_placement((x + _s(110.0), y + _s(40.0)))

    # 4. EAC / ETEK Plan-Approval Drawing Border, Title Block & Symbol Legend
    all_pts = [p for r in result.design.building.rooms for p in r.polygon_mm]
    minx = _s(min(p.x_mm for p in all_pts) - 1000.0)
    maxx = _s(max(p.x_mm for p in all_pts) + 3800.0)
    miny = _s(min(p.y_mm for p in all_pts) - 1800.0)
    maxy = _s(max(p.y_mm for p in all_pts) + 1000.0)

    msp.add_lwpolyline(
        [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    # Right-side legend & EAC/ETEK title panel
    panel_x = maxx - _s(3200.0)
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
        *(
            [
                (f"FIRM: {result.design.branding.firm_name[:32]}", 110.0),
            ]
            if result.design.branding and result.design.branding.firm_name
            else []
        ),
        *(
            [
                (
                    f"ENG: {result.design.branding.engineer_name[:20]} "
                    f"({result.design.branding.etek_reg_no[:12]})",
                    105.0,
                ),
            ]
            if result.design.branding
            and (result.design.branding.engineer_name or result.design.branding.etek_reg_no)
            else []
        ),
        (f"SHA256: {result.design.provenance.input_sha256[:16]}", 105.0),
    ]
    cur_y = maxy - _s(450.0)
    for text, h in legend_lines:
        msp.add_text(
            text, dxfattribs={"layer": "E-TITLE", "height": _s(h)}
        ).set_placement((panel_x + _s(150.0), cur_y))
        cur_y -= _s(max(320.0, h * 2.4))

    return doc


def export_electrical_layout_dxf(
    result: Phase2DesignResult,
    out_path: Path,
    *,
    source_dxf_path: Path | None = None,
) -> Path:
    """Write annotated Phase 2 electrical overlay R2018 DXF file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = render_electrical_layout_dxf(result, source_dxf_path=source_dxf_path)
    doc.saveas(str(out_path))
    return out_path
