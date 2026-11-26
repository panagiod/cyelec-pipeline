"""Stampable R2018 DXF electrical overlay renderer (IEC 60617 blocks, true-scale ISO sheets)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from ezdxf.addons.importer import Importer
from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf
from ezdxf.filemanagement import readfile as read_dxf
from ezdxf.lldxf.const import DXFError

from cyelec.models.building import Room
from cyelec.models.electrical import CircuitCategory
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
    "E-COND-LITE": 50,     # Warm yellow-amber: Lighting conduit runs
    "E-COND-PWR": 140,     # Cool cyan-blue: Socket/ring power conduits
    "E-COND-DED": 200,     # Violet: Dedicated A/C, cooker, EV, PV, 3P feeders
    "E-TITLE": 7,          # White/Black: EAC / ETEK CAD title block & legend
}

_BLOCK_NAMES: dict[DeviceType, str] = {
    DeviceType.LUMINAIRE: "CYELEC_LUMINAIRE",
    DeviceType.SWITCH: "CYELEC_SWITCH",
    DeviceType.SOCKET_TWIN: "CYELEC_SOCKET_13A",
    DeviceType.SPLIT_AC: "CYELEC_AC_ISOL",
    DeviceType.COOKER_OUTLET: "CYELEC_APPL",
    DeviceType.OVEN_OUTLET: "CYELEC_APPL",
    DeviceType.APPLIANCE_OUTLET: "CYELEC_APPL",
    DeviceType.WATER_HEATER_ISOLATOR: "CYELEC_APPL",
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


def _build_multifloor_underlay_doc(result: Phase2DesignResult) -> Drawing | None:
    """Merge each floor's original architect DXF into mm modelspace at exact dx_mm."""
    refs = [
        ref
        for ref in result.design.building.floor_underlays
        if Path(ref.source_file).is_file() and Path(ref.source_file).suffix.lower() == ".dxf"
    ]
    if len(refs) < 2:
        return None

    target = new_dxf("R2018", setup=True)
    target.header["$INSUNITS"] = 4
    target.header["$MEASUREMENT"] = 1
    t_msp = target.modelspace()
    merged_any = False

    for idx, ref in enumerate(refs):
        try:
            src_doc = read_dxf(ref.source_file)
            for lyr in src_doc.layers:
                lname = str(lyr.dxf.name)
                if lname and lname not in target.layers:
                    target.layers.add(lname, color=int(getattr(lyr.dxf, "color", 7)))
            blk_name = f"ARCH_FLOOR_{idx}_{ref.floor[:8]}"
            if blk_name in target.blocks:
                target.blocks.delete_block(blk_name, safe=False)
            importer = Importer(src_doc, target)
            # Copy source modelspace into a dedicated floor block, then insert scaled+shifted
            tmp_blk = src_doc.blocks.new(name=blk_name)
            for ent in list(src_doc.modelspace()):
                try:
                    tmp_blk.add_entity(ent.copy())
                except Exception:
                    continue
            importer.import_block(blk_name)
            importer.finalize()
            s = ref.unit_scale_to_mm
            t_msp.add_blockref(
                blk_name,
                (ref.dx_mm, 0.0),
                dxfattribs={"xscale": s, "yscale": s, "zscale": s},
            )
            merged_any = True
        except (OSError, DXFError):
            continue

    return target if merged_any else None


def _ensure_iec_blocks(doc: Drawing, inv_scale: float) -> None:
    """Define reusable named IEC 60617 AutoCAD BLOCK symbols scaled to modelspace units."""

    def _s(v_mm: float) -> float:
        return v_mm * inv_scale

    # 1. Ceiling Luminaire (circle + 45-deg cross)
    if "CYELEC_LUMINAIRE" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_LUMINAIRE")
        r, d = _s(120.0), _s(85.0)
        blk.add_circle((0.0, 0.0), radius=r, dxfattribs={"layer": "0"})
        blk.add_line((-d, -d), (d, d), dxfattribs={"layer": "0"})
        blk.add_line((-d, d), (d, -d), dxfattribs={"layer": "0"})

    # 2. Light Switch (IEC 60617 circle + angled lever + terminal tick)
    if "CYELEC_SWITCH" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_SWITCH")
        blk.add_circle((0.0, 0.0), radius=_s(65.0), dxfattribs={"layer": "0"})
        blk.add_line((0.0, 0.0), (_s(120.0), _s(120.0)), dxfattribs={"layer": "0"})
        blk.add_line(
            (_s(120.0), _s(120.0)), (_s(155.0), _s(85.0)), dxfattribs={"layer": "0"}
        )

    # 3. Twin 13A BS 1363 Socket (wall-aligned semicircle + base bar + twin ticks)
    if "CYELEC_SOCKET_13A" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_SOCKET_13A")
        sr = _s(90.0)
        blk.add_arc(
            (0.0, 0.0), radius=sr, start_angle=0.0, end_angle=180.0, dxfattribs={"layer": "0"}
        )
        blk.add_line((-sr, 0.0), (sr, 0.0), dxfattribs={"layer": "0"})
        blk.add_line((0.0, sr), (0.0, sr + _s(60.0)), dxfattribs={"layer": "0"})
        blk.add_line(
            (-_s(35.0), sr + _s(35.0)), (_s(35.0), sr + _s(35.0)), dxfattribs={"layer": "0"}
        )

    # 4. Split A/C Isolator (wall-aligned box + diagonal + AC mark)
    if "CYELEC_AC_ISOL" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_AC_ISOL")
        rx, ry = _s(150.0), _s(75.0)
        blk.add_lwpolyline(
            [(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_line((-rx, -ry), (rx, ry), dxfattribs={"layer": "0"})

    # 5. Dedicated Appliance Isolator / FCU (square + inner diamond)
    if "CYELEC_APPL" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_APPL")
        rr = _s(95.0)
        blk.add_lwpolyline(
            [(-rr, -rr), (rr, -rr), (rr, rr), (-rr, rr)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_lwpolyline(
            [(0.0, -rr), (rr, 0.0), (0.0, rr), (-rr, 0.0)],
            close=True,
            dxfattribs={"layer": "0"},
        )

    # 6. Distribution Board IEC 60617 half-filled rectangle
    if "CYELEC_DB" not in doc.blocks:
        blk = doc.blocks.new(name="CYELEC_DB")
        w, h = _s(220.0), _s(110.0)
        blk.add_lwpolyline(
            [(-w, -h), (w, -h), (w, h), (-w, h)],
            close=True,
            dxfattribs={"layer": "0"},
        )
        blk.add_line((-w, -h), (w, h), dxfattribs={"layer": "0"})
        blk.add_line((-w, 0.0), (0.0, h), dxfattribs={"layer": "0"})


def _conduit_layer_for_category(cat: CircuitCategory | None) -> str:
    if cat in (CircuitCategory.LIGHTING, CircuitCategory.EMERGENCY_LIGHTING):
        return "E-COND-LITE"
    if cat in (
        CircuitCategory.SOCKET_RING,
        CircuitCategory.SOCKET_RADIAL,
        CircuitCategory.UPS_SERVER,
    ):
        return "E-COND-PWR"
    return "E-COND-DED"


def _boxes_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 0.0,
) -> bool:
    return not (
        a[2] + pad <= b[0]
        or b[2] + pad <= a[0]
        or a[3] + pad <= b[1]
        or b[3] + pad <= a[1]
    )


def _place_decollided_label(
    x: float,
    y: float,
    text: str,
    text_h: float,
    occupied_boxes: list[tuple[float, float, float, float]],
    inv_scale: float,
) -> tuple[float, float]:
    """Select the first collision-free label insertion point around (x, y) deterministically."""
    w = max(text_h * 0.65 * len(text), text_h * 2.5)
    h = text_h * 1.25
    r1 = 155.0 * inv_scale
    r2 = 310.0 * inv_scale
    r3 = 470.0 * inv_scale
    pad = 25.0 * inv_scale

    candidates: list[tuple[float, float]] = []
    for rad in (r1, r2, r3):
        candidates.extend(
            [
                (x + rad, y + rad * 0.35),              # E-NE
                (x + rad, y - rad * 0.35 - h),          # E-SE
                (x - rad - w, y + rad * 0.35),          # W-NW
                (x - rad - w, y - rad * 0.35 - h),      # W-SW
                (x - w * 0.5, y + rad),                 # N
                (x - w * 0.5, y - rad - h),             # S
                (x + rad * 0.7, y + rad * 0.85),        # NE
                (x - rad * 0.7 - w, y - rad * 0.85 - h),  # SW
            ]
        )

    for cx, cy in candidates:
        cand_box = (cx, cy, cx + w, cy + h)
        if not any(_boxes_overlap(cand_box, ob, pad=pad) for ob in occupied_boxes):
            occupied_boxes.append(cand_box)
            return cx, cy

    # Deterministic non-overlapping vertical stack fallback in extreme density corners
    fx, fy = candidates[0]
    while any(_boxes_overlap((fx, fy, fx + w, fy + h), ob, pad=pad) for ob in occupied_boxes):
        fy += h + pad * 2.0
    occupied_boxes.append((fx, fy, fx + w, fy + h))
    return fx, fy


def render_electrical_layout_dxf(
    result: Phase2DesignResult,
    *,
    source_dxf_path: Path | None = None,
) -> Drawing:
    """Overlay IEC 60617 electrical blocks, offset conduits & true-scale ISO sheets onto DXF."""
    orig_path = _resolve_original_dxf_path(result, source_dxf_path)
    preserved_underlay = False
    inv_scale = 1.0

    mf_doc = _build_multifloor_underlay_doc(result) if orig_path is None else None
    if mf_doc is not None:
        doc = mf_doc
        preserved_underlay = True
        inv_scale = 1.0
    elif orig_path is not None:
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

    _ensure_iec_blocks(doc, inv_scale)
    msp = doc.modelspace()
    spatial = result.spatial

    # Mark DB1 / MDB and Sub-DB anchors cleanly with named CYELEC_DB block
    db_anchor = result.design.building.db_anchor
    db_anchors = [(db_anchor.id, db_anchor.location_mm)]
    for sb in result.design.building.sub_boards:
        db_anchors.append((sb.board_id, sb.location_mm))

    occupied_boxes: list[tuple[float, float, float, float]] = []
    for db_id, db_pt in db_anchors:
        dx, dy = _s(db_pt.x_mm), _s(db_pt.y_mm)
        msp.add_blockref("CYELEC_DB", (dx, dy), dxfattribs={"layer": "E-TITLE"})
        occupied_boxes.append((dx - _s(240.0), dy - _s(130.0), dx + _s(240.0), dy + _s(130.0)))
        lbl_x, lbl_y = _place_decollided_label(
            dx, dy, db_id, _s(110.0), occupied_boxes, inv_scale
        )
        msp.add_text(
            db_id, dxfattribs={"layer": "E-TITLE", "height": _s(110.0)}
        ).set_placement((lbl_x, lbl_y))

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

    # 2. Split-layer & parallel-offset NetworkX conduit runs + homerun markers
    circuit_cat_map = {c.spec.circuit_id: c.spec.category for c in result.design.circuits}

    # Index coincident corridor segments so parallel runs fan out perpendicularly (35 mm pitch)
    seg_circuits: dict[tuple[tuple[int, int], tuple[int, int]], list[str]] = defaultdict(list)
    for rc in spatial.routed_circuits:
        for seg in rc.segments:
            p1 = (round(seg.start_mm.x_mm / 50.0), round(seg.start_mm.y_mm / 50.0))
            p2 = (round(seg.end_mm.x_mm / 50.0), round(seg.end_mm.y_mm / 50.0))
            key = (p1, p2) if p1 <= p2 else (p2, p1)
            if rc.circuit_id not in seg_circuits[key]:
                seg_circuits[key].append(rc.circuit_id)

    pitch_mm = 35.0
    max_fan_mm = 210.0
    for rc in spatial.routed_circuits:
        cond_layer = _conduit_layer_for_category(circuit_cat_map.get(rc.circuit_id))
        for seg_idx, seg in enumerate(rc.segments):
            x1, y1 = seg.start_mm.x_mm, seg.start_mm.y_mm
            x2, y2 = seg.end_mm.x_mm, seg.end_mm.y_mm
            p1 = (round(x1 / 50.0), round(y1 / 50.0))
            p2 = (round(x2 / 50.0), round(y2 / 50.0))
            key = (p1, p2) if p1 <= p2 else (p2, p1)
            occupants = seg_circuits[key]
            n_occ = len(occupants)
            ox_mm, oy_mm = 0.0, 0.0
            if n_occ > 1:
                slot = occupants.index(rc.circuit_id)
                eff_pitch = min(pitch_mm, (2.0 * max_fan_mm) / max(1, n_occ - 1))
                offset = (slot - (n_occ - 1) / 2.0) * eff_pitch
                # Use canonical orientation for consistent perpendicular normal across circuits
                ax, ay, bx, by = (x1, y1, x2, y2) if p1 <= p2 else (x2, y2, x1, y1)
                seg_len = math.hypot(bx - ax, by - ay)
                if seg_len > 1e-3:
                    nx, ny = -(by - ay) / seg_len, (bx - ax) / seg_len
                    ox_mm, oy_mm = nx * offset, ny * offset

            sx1, sy1 = _s(x1 + ox_mm), _s(y1 + oy_mm)
            sx2, sy2 = _s(x2 + ox_mm), _s(y2 + oy_mm)
            msp.add_line((sx1, sy1), (sx2, sy2), dxfattribs={"layer": cond_layer})

            # Crisp homerun arrow tick on the first DB departure segment
            if seg_idx == 0:
                seg_l = math.hypot(sx2 - sx1, sy2 - sy1)
                if seg_l > _s(250.0):
                    ux, uy = (sx2 - sx1) / seg_l, (sy2 - sy1) / seg_l
                    hx = sx1 + ux * _s(220.0)
                    hy = sy1 + uy * _s(220.0)
                    tick = _s(55.0)
                    msp.add_line(
                        (hx, hy),
                        (hx - ux * tick - uy * tick, hy - uy * tick + ux * tick),
                        dxfattribs={"layer": cond_layer},
                    )
                    msp.add_line(
                        (hx, hy),
                        (hx - ux * tick + uy * tick, hy - uy * tick - ux * tick),
                        dxfattribs={"layer": cond_layer},
                    )

    # 3. Placed IEC 60617 named BLOCK references (wall-rotated) + de-collided circuit tags
    # Pre-seed occupied_boxes with every device footprint so labels never print over symbols
    for dev in spatial.devices:
        x, y = _s(dev.location_mm.x_mm), _s(dev.location_mm.y_mm)
        rad = _s(125.0)
        occupied_boxes.append((x - rad, y - rad, x + rad, y + rad))

    for dev in spatial.devices:
        x, y = _s(dev.location_mm.x_mm), _s(dev.location_mm.y_mm)
        tag = f"{dev.circuit_id or '?'}:{dev.device_id.split('-')[-1]}"
        blk_name = _BLOCK_NAMES.get(dev.device_type, "CYELEC_APPL")

        if dev.device_type == DeviceType.LUMINAIRE:
            layer = "E-LITE"
            rot = 0.0
        elif dev.device_type == DeviceType.SWITCH:
            layer = "E-SWIT"
            rot = dev.wall_angle_deg
        elif dev.device_type == DeviceType.SOCKET_TWIN:
            layer = "E-SOCK"
            rot = dev.wall_angle_deg
        elif dev.device_type == DeviceType.SPLIT_AC:
            layer = "E-AC"
            rot = dev.wall_angle_deg
        else:
            layer = "E-APPL"
            rot = dev.wall_angle_deg

        msp.add_blockref(
            blk_name,
            (x, y),
            dxfattribs={"layer": layer, "rotation": rot},
        )
        lx, ly = _place_decollided_label(x, y, tag, _s(65.0), occupied_boxes, inv_scale)
        msp.add_text(tag, dxfattribs={"layer": layer, "height": _s(65.0)}).set_placement((lx, ly))

    # 4. Model-space border ONLY for synthetic fallback (never overwrite real architect DXFs!)
    if not preserved_underlay:
        all_pts = [p for r in result.design.building.rooms for p in r.polygon_mm]
        if all_pts:
            minx = _s(min(p.x_mm for p in all_pts) - 800.0)
            maxx = _s(max(p.x_mm for p in all_pts) + 800.0)
            miny = _s(min(p.y_mm for p in all_pts) - 800.0)
            maxy = _s(max(p.y_mm for p in all_pts) + 800.0)
            msp.add_lwpolyline(
                [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)],
                close=True,
                dxfattribs={"layer": "E-TITLE"},
            )

    # 5. Exact True-Scale ISO Paper-Space Sheet(s) with Graphical Legend, Scale Bar & North Arrow
    all_rooms = result.design.building.rooms
    _add_iso_paper_space_sheet(
        doc,
        result,
        rooms_subset=all_rooms,
        inv_scale=inv_scale,
        floor_tag=None,
        dwg_no="E-101",
    )

    # For multi-floor buildings, also create one dedicated true-scale print sheet per floor!
    distinct_floors = sorted({r.floor for r in all_rooms})
    if len(distinct_floors) >= 2:
        for f_idx, flr in enumerate(distinct_floors, start=2):
            flr_rooms = [r for r in all_rooms if r.floor == flr]
            _add_iso_paper_space_sheet(
                doc,
                result,
                rooms_subset=flr_rooms,
                inv_scale=inv_scale,
                floor_tag=flr,
                dwg_no=f"E-10{f_idx}",
            )

    return doc


def _select_true_scale_sheet(
    span_x_mm: float,
    span_y_mm: float,
) -> tuple[str, float, float, int, float]:
    """Select the smallest ISO A3/A1 sheet & standard metric scale fitting at 100% EXACT scale."""
    # (sheet_label, sheet_w_mm, sheet_h_mm, scale_denom, title_strip_w_mm)
    candidates: list[tuple[str, float, float, int, float]] = [
        ("ISO A3", 420.0, 297.0, 50, 92.0),
        ("ISO A3", 420.0, 297.0, 100, 92.0),
        ("ISO A1", 841.0, 594.0, 50, 130.0),
        ("ISO A1", 841.0, 594.0, 100, 130.0),
        ("ISO A1", 841.0, 594.0, 200, 130.0),
    ]
    margin = 10.0
    for label, sw, sh, denom, strip_w in candidates:
        vp_w = (sw - 2.0 * margin) - strip_w - 6.0
        vp_h = (sh - 2.0 * margin) - 8.0
        if span_x_mm * 1.08 <= vp_w * denom and span_y_mm * 1.08 <= vp_h * denom:
            return label, sw, sh, denom, strip_w
    return candidates[-1]


def _add_iso_paper_space_sheet(
    doc: Drawing,
    result: Phase2DesignResult,
    *,
    rooms_subset: Sequence[Room],
    inv_scale: float = 1.0,
    floor_tag: str | None = None,
    dwg_no: str = "E-101",
) -> None:
    """Create a true-scale ISO A3/A1 Paper-Space tab with right title strip, legend & VIEWPORT."""
    from cyelec.models.building import Room

    rooms = [r for r in rooms_subset if isinstance(r, Room)]
    all_pts = [p for r in rooms for p in r.polygon_mm]
    if not all_pts:
        return

    raw_minx = min(p.x_mm for p in all_pts)
    raw_maxx = max(p.x_mm for p in all_pts)
    raw_miny = min(p.y_mm for p in all_pts)
    raw_maxy = max(p.y_mm for p in all_pts)
    span_x_mm = max(1000.0, raw_maxx - raw_minx)
    span_y_mm = max(1000.0, raw_maxy - raw_miny)

    sheet_label, sheet_w, sheet_h, scale_denom, strip_w = _select_true_scale_sheet(
        span_x_mm, span_y_mm
    )
    size_tag = "A3" if sheet_w < 500.0 else "A1"
    tab_prefix = f"EAC-{floor_tag}" if floor_tag else "EAC"
    tab_name = f"{tab_prefix}-{size_tag}-1-{scale_denom}"

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

    # Outer paper boundary & inner printable border (Paper-Space mm)
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

    # Right-side Cyprus EAC / ETEK Title Strip + Graphical IEC 60617 Legend
    strip_x0 = inner_x1 - strip_w
    psp.add_line((strip_x0, inner_y0), (strip_x0, inner_y1), dxfattribs={"layer": "E-TITLE"})

    # Graphical IEC 60617 Legend (upper right panel)
    leg_top = inner_y1 - 8.0
    psp.add_text(
        "IEC 60617 SYMBOL LEGEND", dxfattribs={"layer": "E-TITLE", "height": 3.0}
    ).set_placement((strip_x0 + 4.0, leg_top))
    psp.add_line(
        (strip_x0, leg_top - 3.0), (inner_x1, leg_top - 3.0), dxfattribs={"layer": "E-TITLE"}
    )

    sym_items = [
        ("E-LITE", "Ceiling Luminaire", "lum"),
        ("E-SWIT", "Light Switch (1.2m)", "sw"),
        ("E-SOCK", "Twin 13A Socket", "sock"),
        ("E-AC", "Split A/C Isolator", "ac"),
        ("E-APPL", "Dedicated Appliance", "appl"),
        ("E-COND-LITE", "Lighting Conduit", "line"),
        ("E-COND-PWR", "Power Conduit", "line"),
        ("E-COND-DED", "Dedicated Feeder", "line"),
        ("E-WETZONE-EXCL", "Sec.701 Exclusion", "box"),
    ]
    row_step = 11.0 if size_tag == "A3" else 14.0
    for idx, (lyr, desc, kind) in enumerate(sym_items):
        cy = leg_top - 10.0 - idx * row_step
        sx = strip_x0 + 9.0
        if kind == "lum":
            psp.add_circle((sx, cy), radius=2.6, dxfattribs={"layer": lyr})
            psp.add_line((sx - 1.8, cy - 1.8), (sx + 1.8, cy + 1.8), dxfattribs={"layer": lyr})
            psp.add_line((sx - 1.8, cy + 1.8), (sx + 1.8, cy - 1.8), dxfattribs={"layer": lyr})
        elif kind == "sw":
            psp.add_circle((sx, cy), radius=1.6, dxfattribs={"layer": lyr})
            psp.add_line((sx, cy), (sx + 3.0, cy + 3.0), dxfattribs={"layer": lyr})
        elif kind == "sock":
            psp.add_arc(
                (sx, cy - 1.0),
                radius=2.4,
                start_angle=0.0,
                end_angle=180.0,
                dxfattribs={"layer": lyr},
            )
            psp.add_line((sx - 2.4, cy - 1.0), (sx + 2.4, cy - 1.0), dxfattribs={"layer": lyr})
        elif kind == "ac":
            psp.add_lwpolyline(
                [
                    (sx - 3.2, cy - 1.8),
                    (sx + 3.2, cy - 1.8),
                    (sx + 3.2, cy + 1.8),
                    (sx - 3.2, cy + 1.8),
                ],
                close=True,
                dxfattribs={"layer": lyr},
            )
            psp.add_line((sx - 3.2, cy - 1.8), (sx + 3.2, cy + 1.8), dxfattribs={"layer": lyr})
        elif kind == "appl":
            psp.add_lwpolyline(
                [
                    (sx - 2.4, cy - 2.4),
                    (sx + 2.4, cy - 2.4),
                    (sx + 2.4, cy + 2.4),
                    (sx - 2.4, cy + 2.4),
                ],
                close=True,
                dxfattribs={"layer": lyr},
            )
        elif kind == "line":
            psp.add_line((sx - 4.0, cy), (sx + 4.0, cy), dxfattribs={"layer": lyr})
        else:
            psp.add_lwpolyline(
                [
                    (sx - 3.2, cy - 2.0),
                    (sx + 3.2, cy - 2.0),
                    (sx + 3.2, cy + 2.0),
                    (sx - 3.2, cy + 2.0),
                ],
                close=True,
                dxfattribs={"layer": lyr},
            )
        psp.add_text(desc, dxfattribs={"layer": "E-TITLE", "height": 2.4}).set_placement(
            (strip_x0 + 16.0, cy - 1.1)
        )

    # Bottom-Right Cyprus EAC / ETEK Title Block inside right strip
    tb_top = inner_y0 + (98.0 if size_tag == "A3" else 132.0)
    psp.add_line((strip_x0, tb_top), (inner_x1, tb_top), dxfattribs={"layer": "E-TITLE"})

    eac = result.design.eac_compliance
    br = result.design.branding
    firm = br.firm_name if br and br.firm_name else "ETEK LICENSED PRACTICE"
    eng = br.engineer_name if br and br.engineer_name else "ETEK REVIEW PENDING"
    reg = br.etek_reg_no if br and br.etek_reg_no else "ETEK-E"
    floor_lbl = f" ({floor_tag})" if floor_tag else ""
    earth_txt = (
        f"EARTH: {result.design.earthing_system.value} "
        f"({result.design.ze_or_ra_ohm:.1f} OHM)"
    )
    tb_rows = [
        (tb_top - 10.0, f"EAC / ETEK LAYOUT{floor_lbl}", 2.8),
        (tb_top - 20.0, f"{sheet_label} | SCALE 1:{scale_denom} (METRIC)", 2.3),
        (tb_top - 32.0, f"PROJ: {result.design.building.project_name[:34]}", 2.1),
        (tb_top - 44.0, f"SUPPLY: {eac.recommended_supply_tier[:30]}", 2.1),
        (tb_top - 54.0, earth_txt, 2.1),
        (tb_top - 66.0, f"FIRM: {firm[:36]}", 2.1),
        (tb_top - 76.0, f"ENG: {eng[:34]}", 2.1),
        (tb_top - 85.0, f"REG: {reg[:28]} | {dwg_no}", 2.1),
        (tb_top - 94.0, f"SHA: {result.design.provenance.input_sha256[:16]} | SIGN-OFF", 1.9),
    ]
    for ty, txt, h in tb_rows:
        if ty > inner_y0 + 1.5:
            psp.add_text(txt, dxfattribs={"layer": "E-TITLE", "height": h}).set_placement(
                (strip_x0 + 3.5, ty)
            )

    # North Arrow inside right strip above title block
    nx, ny = strip_x0 + strip_w * 0.5, tb_top + 16.0
    psp.add_circle((nx, ny), radius=6.5, dxfattribs={"layer": "E-TITLE"})
    psp.add_lwpolyline(
        [(nx, ny + 6.5), (nx - 2.8, ny - 4.5), (nx, ny - 2.0), (nx + 2.8, ny - 4.5)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    psp.add_text("N", dxfattribs={"layer": "E-TITLE", "height": 3.2}).set_placement(
        (nx - 1.3, ny + 8.0)
    )

    # Metric Scale Bar inside right strip above North Arrow (0 .. 1m .. 2m .. 5m at EXACT 1:scale)
    sb_y0 = tb_top + 34.0
    mm_per_m = 1000.0 / float(scale_denom)
    max_m = 3 if (5.0 * mm_per_m) > (strip_w - 14.0) else 5
    bar_len = float(max_m) * mm_per_m
    sb_x0 = strip_x0 + (strip_w - bar_len) * 0.5
    psp.add_lwpolyline(
        [
            (sb_x0, sb_y0),
            (sb_x0 + bar_len, sb_y0),
            (sb_x0 + bar_len, sb_y0 + 2.8),
            (sb_x0, sb_y0 + 2.8),
        ],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    ticks = (0, 1, 2, 3) if max_m == 3 else (0, 1, 2, 5)
    for m_tick in ticks:
        tx = sb_x0 + m_tick * mm_per_m
        psp.add_line((tx, sb_y0), (tx, sb_y0 + 4.2), dxfattribs={"layer": "E-TITLE"})
        psp.add_text(f"{m_tick}m", dxfattribs={"layer": "E-TITLE", "height": 2.2}).set_placement(
            (tx - 1.4, sb_y0 + 5.2)
        )
    psp.add_text(
        f"SCALE 1:{scale_denom} (METRIC)", dxfattribs={"layer": "E-TITLE", "height": 2.4}
    ).set_placement((sb_x0, sb_y0 - 4.8))

    # EXACT True-Scale DXF Paper-Space VIEWPORT (zero silent scale distortion!)
    vp_w = (strip_x0 - inner_x0) - 6.0
    vp_h = (inner_y1 - inner_y0) - 8.0
    vp_cx = inner_x0 + 3.0 + (vp_w / 2.0)
    vp_cy = inner_y0 + 4.0 + (vp_h / 2.0)
    model_cx = ((raw_minx + raw_maxx) / 2.0) * inv_scale
    model_cy = ((raw_miny + raw_maxy) / 2.0) * inv_scale
    # Strictly lock model view_height to exact paper height * scale_denom!
    view_height_model = (vp_h * float(scale_denom)) * inv_scale
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
    """Write stampable Phase 2 electrical overlay R2018 DXF with IEC blocks & true-scale sheets."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = render_electrical_layout_dxf(result, source_dxf_path=source_dxf_path)
    doc.saveas(str(out_path))
    return out_path
