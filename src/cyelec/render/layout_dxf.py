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

    # 5. ISO A3 (1:50) / A1 (1:100) Paper-Space Sheet Layout with Scale Bar & North Arrow
    _add_iso_paper_space_sheet(doc, result, inv_scale=inv_scale)
    return doc


def _add_iso_paper_space_sheet(
    doc: Drawing,
    result: Phase2DesignResult,
    *,
    inv_scale: float = 1.0,
) -> None:
    """Create a print-ready ISO A3 (1:50) or ISO A1 (1:100) paper-space tab with DXF VIEWPORT."""
    rooms = result.design.building.rooms
    all_pts = [p for r in rooms for p in r.polygon_mm]
    if not all_pts:
        return

    raw_minx = min(p.x_mm for p in all_pts)
    raw_maxx = max(p.x_mm for p in all_pts)
    raw_miny = min(p.y_mm for p in all_pts)
    raw_maxy = max(p.y_mm for p in all_pts)
    span_x_mm = max(1000.0, raw_maxx - raw_minx)
    span_y_mm = max(1000.0, raw_maxy - raw_miny)

    # Choose ISO A3 (420x297 mm @ 1:50) for <= 16m spans, else ISO A1 (841x594 mm @ 1:100)
    if span_x_mm <= 16_000.0 and span_y_mm <= 12_000.0:
        sheet_label = "ISO A3"
        sheet_w, sheet_h = 420.0, 297.0
        scale_denom = 50
        tab_name = "EAC-A3-1-50"
    else:
        sheet_label = "ISO A1"
        sheet_w, sheet_h = 841.0, 594.0
        scale_denom = 100
        tab_name = "EAC-A1-1-100"

    if tab_name in doc.layouts:
        doc.layouts.delete(tab_name)
    psp = doc.layouts.new(tab_name)
    psp.page_setup(
        size=(sheet_w, sheet_h),
        margins=(10.0, 10.0, 10.0, 10.0),
        units="mm",
    )

    margin = 10.0
    inner_x0, inner_y0 = margin, margin
    inner_x1, inner_y1 = sheet_w - margin, sheet_h - margin

    # Outer sheet boundary & printable margin frame (all Paper-Space coordinates in mm)
    psp.add_lwpolyline(
        [(0.0, 0.0), (sheet_w, 0.0), (sheet_w, sheet_h), (0.0, sheet_h)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    psp.add_lwpolyline(
        [
            (inner_x0, inner_y0),
            (inner_x1, inner_y0),
            (inner_x1, inner_y1),
            (inner_x0, inner_y1),
        ],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )

    # Bottom-right Cyprus EAC / ETEK Title Block (175 mm x 58 mm)
    tb_w, tb_h = 175.0, 58.0
    tb_x0 = inner_x1 - tb_w
    tb_y0 = inner_y0
    psp.add_lwpolyline(
        [
            (tb_x0, tb_y0),
            (inner_x1, tb_y0),
            (inner_x1, tb_y0 + tb_h),
            (tb_x0, tb_y0 + tb_h),
        ],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    for dy in (14.0, 28.0, 42.0):
        psp.add_line((tb_x0, tb_y0 + dy), (inner_x1, tb_y0 + dy), dxfattribs={"layer": "E-TITLE"})

    eac = result.design.eac_compliance
    br = result.design.branding
    firm = br.firm_name if br and br.firm_name else "ETEK LICENSED PRACTICE"
    eng = br.engineer_name if br and br.engineer_name else "ETEK REVIEW PENDING"
    reg = br.etek_reg_no if br and br.etek_reg_no else "ETEK-E"
    tb_rows = [
        (tb_y0 + 48.0, f"EAC / ETEK ELECTRICAL LAYOUT  |  {sheet_label} @ 1:{scale_denom}", 3.4),
        (tb_y0 + 34.0, f"PROJECT: {result.design.building.project_name[:40]}", 2.8),
        (
            tb_y0 + 19.0,
            f"SUPPLY: {eac.recommended_supply_tier} | {result.design.earthing_system.value}",
            2.5,
        ),
        (tb_y0 + 5.0, f"FIRM: {firm[:20]} | ENG: {eng[:14]} ({reg[:20]}) | E-101", 2.2),
    ]
    for ty, txt, h in tb_rows:
        psp.add_text(txt, dxfattribs={"layer": "E-TITLE", "height": h}).set_placement(
            (tb_x0 + 4.0, ty)
        )

    # Graphical Metric Scale Bar (bottom-left of paper sheet: 0 .. 1m .. 2m .. 5m)
    sb_x0 = inner_x0 + 12.0
    sb_y0 = inner_y0 + 12.0
    mm_per_m = 1000.0 / float(scale_denom)  # 20 mm/m at 1:50; 10 mm/m at 1:100
    bar_len = 5.0 * mm_per_m
    psp.add_lwpolyline(
        [
            (sb_x0, sb_y0),
            (sb_x0 + bar_len, sb_y0),
            (sb_x0 + bar_len, sb_y0 + 3.0),
            (sb_x0, sb_y0 + 3.0),
        ],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    for m_tick in (0, 1, 2, 5):
        tx = sb_x0 + m_tick * mm_per_m
        psp.add_line((tx, sb_y0), (tx, sb_y0 + 4.5), dxfattribs={"layer": "E-TITLE"})
        psp.add_text(f"{m_tick}m", dxfattribs={"layer": "E-TITLE", "height": 2.4}).set_placement(
            (tx - 1.5, sb_y0 + 6.0)
        )
    psp.add_text(
        f"SCALE 1:{scale_denom} (METRIC)", dxfattribs={"layer": "E-TITLE", "height": 2.6}
    ).set_placement((sb_x0, sb_y0 - 5.0))

    # North Arrow (top-right inside margin)
    nx, ny = inner_x1 - 18.0, inner_y1 - 20.0
    psp.add_circle((nx, ny), radius=7.0, dxfattribs={"layer": "E-TITLE"})
    psp.add_lwpolyline(
        [(nx, ny + 7.0), (nx - 3.0, ny - 5.0), (nx, ny - 2.0), (nx + 3.0, ny - 5.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    psp.add_text("N", dxfattribs={"layer": "E-TITLE", "height": 3.5}).set_placement(
        (nx - 1.5, ny + 8.5)
    )

    # Scaled DXF Paper-Space VIEWPORT centered on modelspace floorplan + E-* layers
    vp_w = (inner_x1 - inner_x0) - 20.0
    vp_h = (inner_y1 - (tb_y0 + tb_h)) - 14.0
    vp_cx = (inner_x0 + inner_x1) / 2.0
    vp_cy = (tb_y0 + tb_h) + (vp_h / 2.0) + 6.0
    model_cx = ((raw_minx + raw_maxx) / 2.0) * inv_scale
    model_cy = ((raw_miny + raw_maxy) / 2.0) * inv_scale
    # Modelspace view height in model units corresponding to paper viewport height at 1:scale_denom
    view_height_model = max(
        (vp_h * float(scale_denom)) * inv_scale,
        (span_y_mm * 1.25) * inv_scale,
    )
    psp.add_viewport(
        center=(vp_cx, vp_cy),
        size=(vp_w, vp_h),
        view_center_point=(model_cx, model_cy),
        view_height=view_height_model,
        dxfattribs={"layer": "E-TITLE"},
    )


def export_electrical_layout_dxf(
    result: Phase2DesignResult,
    out_path: Path,
    *,
    source_dxf_path: Path | None = None,
) -> Path:
    """Write annotated Phase 2 electrical overlay R2018 DXF file with ISO A3/A1 paper sheet."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = render_electrical_layout_dxf(result, source_dxf_path=source_dxf_path)
    doc.saveas(str(out_path))
    return out_path
