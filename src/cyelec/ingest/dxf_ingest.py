"""Deterministic R12-R2018 DXF architectural plan ingestion into BuildingModel via ezdxf+shapely."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Literal

from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.entities.mtext import MText
from ezdxf.entities.polyline import Polyline
from ezdxf.entities.text import Text
from ezdxf.filemanagement import readfile as read_dxf
from shapely.geometry import LineString, Point, Polygon

from cyelec.ingest.room_classifier import (
    RoomClassifierFallback,
    classify_room_label,
    clean_room_label,
)
from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    DoorOpening,
    PlumbingFixture,
    Point2D,
    Room,
    RoomType,
    WallSegment,
    WindowOpening,
)
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.standards_loader import StandardsBundle, load_standards, normalize_label

_INSUNITS_TO_MM: dict[int, float] = {
    1: 25.4,     # Inches
    2: 304.8,    # Feet
    4: 1.0,      # Millimeters
    5: 10.0,     # Centimeters
    6: 1000.0,   # Meters
}

_ROOM_LAYER_TOKENS = ("A-ROOM", "ROOM", "SPACE", "AREA", "ZONE")
_WALL_LAYER_TOKENS = ("A-WALL", "WALL", "ΤΟΙΧ")
_DOOR_LAYER_TOKENS = ("A-DOOR", "DOOR", "ΠΟΡΤ")
_WIND_LAYER_TOKENS = ("A-WIND", "WIND", "GLAZ", "ΠΑΡΑΘ")
_FIXT_LAYER_TOKENS = ("A-FIXT", "FIXT", "SANIT", "PLUMB", "BATH", "ΕΙΔΗ")
_TEXT_LAYER_TOKENS = ("A-TEXT", "TEXT", "LABEL", "ANNO", "ROOM-TXT")
_DB_LAYER_TOKENS = ("A-DB", "E-DB", "DB", "PANEL", "ΠΙΝΑΚ")


def _layer_matches(layer: str, tokens: tuple[str, ...]) -> bool:
    up = layer.upper()
    return any(tok in up for tok in tokens)


def _extract_poly_coords(entity: DXFGraphic, scale: float) -> list[tuple[float, float]] | None:
    pts: list[tuple[float, float]] = []
    if isinstance(entity, LWPolyline):
        raw = list(entity.get_points("xy"))
        if len(raw) < 3:
            return None
        is_closed = bool(entity.closed) or (
            math.hypot(raw[0][0] - raw[-1][0], raw[0][1] - raw[-1][1]) < 1e-3
        )
        if not is_closed:
            return None
        pts = [(float(x) * scale, float(y) * scale) for x, y in raw]
    elif isinstance(entity, Polyline):
        raw_v = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        if len(raw_v) < 3:
            return None
        is_closed = bool(entity.is_closed) or (
            math.hypot(raw_v[0][0] - raw_v[-1][0], raw_v[0][1] - raw_v[-1][1]) < 1e-3
        )
        if not is_closed:
            return None
        pts = [(x * scale, y * scale) for x, y in raw_v]
    else:
        return None

    # Remove duplicate closing vertex if present
    if len(pts) >= 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-3:
        pts = pts[:-1]
    return pts if len(pts) >= 3 else None


def _extract_text_item(entity: DXFGraphic, scale: float) -> tuple[str, tuple[float, float]] | None:
    if isinstance(entity, MText):
        text = str(entity.plain_text()).strip()
        ins = entity.dxf.insert
        return (text, (float(ins.x) * scale, float(ins.y) * scale)) if text else None
    if isinstance(entity, Text):
        text = str(entity.dxf.text).strip()
        ins = entity.dxf.insert
        return (text, (float(ins.x) * scale, float(ins.y) * scale)) if text else None
    return None


def _infer_unit_scale(
    insunits: int,
    raw_extents_max: float,
    flags: list[ReviewFlag],
) -> float:
    if insunits in _INSUNITS_TO_MM:
        return _INSUNITS_TO_MM[insunits]
    if raw_extents_max <= 0.0:
        return 1.0
    if raw_extents_max < 100.0:
        scale = 1000.0
        guessed = "metres"
    elif raw_extents_max < 1000.0:
        scale = 10.0
        guessed = "centimetres"
    else:
        scale = 1.0
        guessed = "millimetres"
    flags.append(
        ReviewFlag(
            code="DXF_UNITLESS_INFERRED",
            severity=ReviewSeverity.WARNING,
            category="geometry",
            message=(
                f"DXF $INSUNITS={insunits} is unitless/unsupported; inferred {guessed} "
                f"(scale={scale:g} mm/unit) from geometry span {raw_extents_max:.1f}. "
                "Verify drawing scale."
            ),
        )
    )
    return scale


def _classify_fixture_type(
    label: str | None,
    poly: Polygon,
    room_is_wet: bool,
) -> Literal["bathtub", "shower_tray", "basin", "wc", "sink"]:
    if label:
        norm = normalize_label(label)
        if any(k in norm for k in ("bathtub", "bath", "μπανιερα")):
            return "bathtub"
        if any(k in norm for k in ("shower", "ντους", "ντουζιερα")):
            return "shower_tray"
        if any(k in norm for k in ("basin", "washbasin", "νιπτηρ")):
            return "basin"
        if any(k in norm for k in ("sink", "νεροχυτ")):
            return "sink"
        if any(k in norm for k in ("wc", "toilet", "λεκαν")):
            return "wc"

    minx, miny, maxx, maxy = poly.bounds
    dims = sorted([maxx - minx, maxy - miny])
    short_mm, long_mm = dims[0], dims[1]
    if room_is_wet:
        if long_mm >= 1350.0 and 550.0 <= short_mm <= 1050.0:
            return "bathtub"
        if 650.0 <= short_mm <= 1250.0 and 650.0 <= long_mm <= 1350.0:
            return "shower_tray"
        return "basin"
    return "sink"


def _infer_building_type(rooms: list[Room]) -> BuildingType:
    bed_count = sum(
        1 for r in rooms if r.normalized_type in (RoomType.MASTER_BEDROOM, RoomType.BEDROOM)
    )
    if bed_count >= 3:
        return BuildingType.APARTMENT_3BED_PLUS
    if bed_count == 2:
        return BuildingType.APARTMENT_2BED
    if bed_count == 1:
        return BuildingType.APARTMENT_1BED
    return BuildingType.STUDIO


def _find_containing_room(
    pt: Point,
    room_polys: list[tuple[str, Polygon]],
    *,
    max_fallback_dist_mm: float = 1500.0,
) -> str | None:
    for rid, poly in room_polys:
        if poly.contains(pt) or poly.touches(pt):
            return rid
    best_rid: str | None = None
    best_dist = float("inf")
    for rid, poly in room_polys:
        d = float(poly.distance(pt))
        if d < best_dist:
            best_dist = d
            best_rid = rid
    return best_rid if best_dist <= max_fallback_dist_mm else None


def ingest_dxf(
    dxf_path: Path,
    *,
    standards: StandardsBundle | None = None,
    standards_dir: Path | None = None,
    project_name: str | None = None,
    building_type: BuildingType | None = None,
    pv_capacity_kva: float = 0.0,
    tier2_fallback: RoomClassifierFallback | None = None,
) -> tuple[BuildingModel, list[ReviewFlag]]:
    """Ingest a DXF floorplan into a validated BuildingModel plus ingestion ReviewFlags."""
    if not dxf_path.is_file():
        raise FileNotFoundError(f"DXF file not found: {dxf_path}")

    std = standards or load_standards(standards_dir)
    sha256 = hashlib.sha256(dxf_path.read_bytes()).hexdigest()
    doc = read_dxf(str(dxf_path))

    flags: list[ReviewFlag] = []
    auditor = doc.audit()
    if auditor.errors:
        flags.append(
            ReviewFlag(
                code="DXF_AUDIT_ERRORS",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    f"Input DXF '{dxf_path.name}' reported {len(auditor.errors)} ezdxf audit "
                    "issue(s); geometry verified best-effort."
                ),
            )
        )

    msp = doc.modelspace()
    all_entities = list(msp)

    # First pass: compute raw coordinate span in case $INSUNITS is unitless
    xs: list[float] = []
    ys: list[float] = []
    for ent in all_entities:
        t = ent.dxftype()
        if t == "LINE":
            xs.extend([float(ent.dxf.start.x), float(ent.dxf.end.x)])
            ys.extend([float(ent.dxf.start.y), float(ent.dxf.end.y)])
        elif isinstance(ent, LWPolyline):
            for px, py in ent.get_points("xy"):
                xs.append(float(px))
                ys.append(float(py))
    span = max((max(xs) - min(xs)) if xs else 0.0, (max(ys) - min(ys)) if ys else 0.0)
    insunits = int(doc.header.get("$INSUNITS", 0))
    scale = _infer_unit_scale(insunits, span, flags)

    # Collect text labels & room polygons
    room_raw_polys: list[Polygon] = []
    text_labels: list[tuple[str, Point, str]] = []  # (text, point, layer)
    fixture_polys: list[Polygon] = []
    fixture_texts: list[tuple[str, Point]] = []
    wall_lines: list[tuple[Point2D, Point2D]] = []
    door_arcs: list[tuple[Point2D, float, float, float]] = []
    door_leaves: list[tuple[Point2D, Point2D]] = []
    window_lines: list[tuple[Point2D, Point2D]] = []
    db_points: list[tuple[str, Point2D]] = []

    for ent in all_entities:
        layer = str(ent.dxf.layer)
        dtype = ent.dxftype()

        # Room boundary polygons
        if _layer_matches(layer, _ROOM_LAYER_TOKENS):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                poly = Polygon(coords)
                if not poly.is_valid:
                    poly = poly.buffer(0.0)
                if isinstance(poly, Polygon) and poly.area >= 500_000.0:  # >= 0.5 m²
                    room_raw_polys.append(poly)

        # Wall segments
        elif _layer_matches(layer, _WALL_LAYER_TOKENS):
            if dtype == "LINE":
                wall_lines.append(
                    (
                        Point2D(
                            x_mm=float(ent.dxf.start.x) * scale,
                            y_mm=float(ent.dxf.start.y) * scale,
                        ),
                        Point2D(
                            x_mm=float(ent.dxf.end.x) * scale,
                            y_mm=float(ent.dxf.end.y) * scale,
                        ),
                    )
                )
            elif isinstance(ent, LWPolyline):
                pts = [(float(x) * scale, float(y) * scale) for x, y in ent.get_points("xy")]
                for a, b in zip(pts, pts[1:], strict=False):
                    wall_lines.append(
                        (Point2D(x_mm=a[0], y_mm=a[1]), Point2D(x_mm=b[0], y_mm=b[1]))
                    )
                if ent.closed and len(pts) >= 3:
                    wall_lines.append(
                        (
                            Point2D(x_mm=pts[-1][0], y_mm=pts[-1][1]),
                            Point2D(x_mm=pts[0][0], y_mm=pts[0][1]),
                        )
                    )

        # Doors (arcs + leaf lines)
        elif _layer_matches(layer, _DOOR_LAYER_TOKENS):
            if dtype == "ARC":
                center = Point2D(
                    x_mm=float(ent.dxf.center.x) * scale,
                    y_mm=float(ent.dxf.center.y) * scale,
                )
                door_arcs.append(
                    (
                        center,
                        float(ent.dxf.radius) * scale,
                        float(ent.dxf.start_angle),
                        float(ent.dxf.end_angle),
                    )
                )
            elif dtype == "LINE":
                door_leaves.append(
                    (
                        Point2D(
                            x_mm=float(ent.dxf.start.x) * scale,
                            y_mm=float(ent.dxf.start.y) * scale,
                        ),
                        Point2D(
                            x_mm=float(ent.dxf.end.x) * scale,
                            y_mm=float(ent.dxf.end.y) * scale,
                        ),
                    )
                )

        # Windows
        elif _layer_matches(layer, _WIND_LAYER_TOKENS):
            if dtype == "LINE":
                window_lines.append(
                    (
                        Point2D(
                            x_mm=float(ent.dxf.start.x) * scale,
                            y_mm=float(ent.dxf.start.y) * scale,
                        ),
                        Point2D(
                            x_mm=float(ent.dxf.end.x) * scale,
                            y_mm=float(ent.dxf.end.y) * scale,
                        ),
                    )
                )

        # Plumbing fixtures
        elif _layer_matches(layer, _FIXT_LAYER_TOKENS):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                fpoly = Polygon(coords)
                if fpoly.is_valid and fpoly.area >= 40_000.0:  # >= 200x200 mm
                    fixture_polys.append(fpoly)
            else:
                txt = _extract_text_item(ent, scale)
                if txt is not None:
                    fixture_texts.append((txt[0], Point(txt[1])))

        # Distribution board anchor
        elif _layer_matches(layer, _DB_LAYER_TOKENS):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                c = Polygon(coords).centroid
                db_points.append(("DB1", Point2D(x_mm=float(c.x), y_mm=float(c.y))))
            else:
                txt = _extract_text_item(ent, scale)
                if txt is not None:
                    db_points.append(
                        (txt[0].strip() or "DB1", Point2D(x_mm=txt[1][0], y_mm=txt[1][1]))
                    )

        # Architectural text labels (A-TEXT or generic text layers)
        if _layer_matches(layer, _TEXT_LAYER_TOKENS) or (
            dtype in ("TEXT", "MTEXT")
            and not _layer_matches(layer, _FIXT_LAYER_TOKENS)
            and not _layer_matches(layer, _DB_LAYER_TOKENS)
        ):
            txt = _extract_text_item(ent, scale)
            if txt is not None:
                text_labels.append((txt[0], Point(txt[1]), layer))

    if not room_raw_polys:
        raise ValueError(
            f"No closed room polygons (>= 0.5 m²) found on room layers in DXF '{dxf_path}'."
        )

    # Sort rooms deterministically (top-to-bottom, left-to-right by centroid)
    room_raw_polys.sort(key=lambda p: (-round(p.centroid.y, 1), round(p.centroid.x, 1)))

    # Match labels to rooms & classify deterministically
    rooms: list[Room] = []
    room_poly_pairs: list[tuple[str, Polygon]] = []
    tmpl_std = std.room_templates

    for idx, poly in enumerate(room_raw_polys, start=1):
        rid = f"R{idx}"
        room_poly_pairs.append((rid, poly))

        inside_texts = [t for t, pt, _ in text_labels if poly.contains(pt) or poly.touches(pt)]
        if inside_texts:
            raw_label = clean_room_label(inside_texts[0])
        else:
            raw_label = f"Unnamed Room {idx}"
            flags.append(
                ReviewFlag(
                    code="ROOM_LABEL_MISSING",
                    severity=ReviewSeverity.WARNING,
                    category="geometry",
                    message=(
                        f"Room {rid} ({poly.area / 1e6:.2f} m²) has no enclosing DXF text label; "
                        "defaulted conservatively to 'storage'."
                    ),
                )
            )

        cls_res = classify_room_label(
            raw_label,
            tmpl_std,
            room_id=rid,
            tier2_fallback=tier2_fallback,
        )
        if cls_res.flag is not None and inside_texts:
            flags.append(cls_res.flag)

        rt = tmpl_std.rooms[cls_res.normalized_type]
        ring_coords = list(poly.exterior.coords)[:-1]
        poly_pts = [
            Point2D(x_mm=round(float(x), 2), y_mm=round(float(y), 2))
            for x, y in ring_coords
        ]
        area_m2 = round(float(poly.area) / 1_000_000.0, 4)

        rooms.append(
            Room(
                id=rid,
                raw_label=cls_res.cleaned_label or raw_label,
                normalized_type=cls_res.normalized_type,
                polygon_mm=poly_pts,
                area_m2=area_m2,
                is_wet_room=rt.is_wet_room,
                is_outdoor=rt.is_outdoor,
                ambient_temp_c=(
                    std.eac_rules.ambient_defaults_c.roof_outdoor_c if rt.is_outdoor else None
                ),
                fixtures=[],
            )
        )

    rooms_by_id = {r.id: r for r in rooms}

    # Attach plumbing fixtures to their enclosing room
    for f_idx, fpoly in enumerate(fixture_polys, start=1):
        target_rid = _find_containing_room(
            fpoly.centroid, room_poly_pairs, max_fallback_dist_mm=500.0
        )
        if target_rid is None:
            continue
        room_obj = rooms_by_id[target_rid]
        matched_label: str | None = None
        for ftxt, fpt in fixture_texts:
            if fpoly.contains(fpt) or fpoly.distance(fpt) < 400.0:
                matched_label = ftxt
                break
        ftype = _classify_fixture_type(matched_label, fpoly, room_obj.is_wet_room)
        fcoords = list(fpoly.exterior.coords)[:-1]
        fixt = PlumbingFixture(
            id=f"FIXT-{f_idx}",
            room_id=target_rid,
            fixture_type=ftype,
            polygon_mm=[
                Point2D(x_mm=round(float(x), 2), y_mm=round(float(y), 2)) for x, y in fcoords
            ],
        )
        room_obj.fixtures.append(fixt)
        if ftype in ("bathtub", "shower_tray") and not room_obj.is_wet_room:
            room_obj.is_wet_room = True

    # Build WallSegments (tag true exterior perimeter edges via exact room-union boundary)
    from shapely.ops import unary_union

    outer_ring = unary_union(room_raw_polys).boundary

    walls: list[WallSegment] = []
    for w_idx, (p0, p1) in enumerate(wall_lines, start=1):
        if math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm) < 10.0:
            continue
        seg = LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)])
        is_ext = float(outer_ring.distance(seg.interpolate(0.5, normalized=True))) < 50.0
        walls.append(
            WallSegment(
                id=f"W-{w_idx}",
                start=Point2D(x_mm=round(p0.x_mm, 2), y_mm=round(p0.y_mm, 2)),
                end=Point2D(x_mm=round(p1.x_mm, 2), y_mm=round(p1.y_mm, 2)),
                thickness_mm=250.0 if is_ext else 150.0,
                is_exterior=is_ext,
            )
        )

    # Build DoorOpenings from DXF swing arcs + matching leaf lines
    doors: list[DoorOpening] = []
    for d_idx, (center, radius, start_ang, end_ang) in enumerate(door_arcs, start=1):
        mid_rad = math.radians(((start_ang + ((end_ang - start_ang) % 360.0) / 2.0)) % 360.0)
        probe = Point(
            center.x_mm + 0.5 * radius * math.cos(mid_rad),
            center.y_mm + 0.5 * radius * math.sin(mid_rad),
        )
        target_rid = _find_containing_room(probe, room_poly_pairs) or rooms[0].id

        # Match leaf line starting at hinge to determine ccw vs cw orientation
        swing_dir: Literal["cw", "ccw"] = "ccw"
        angle_deg = start_ang % 360.0
        for ls, le in door_leaves:
            if math.hypot(ls.x_mm - center.x_mm, ls.y_mm - center.y_mm) < 25.0:
                leaf_ang = math.degrees(math.atan2(le.y_mm - ls.y_mm, le.x_mm - ls.x_mm)) % 360.0
                if abs(((leaf_ang - start_ang + 180.0) % 360.0) - 180.0) < 15.0:
                    swing_dir = "cw"
                    angle_deg = end_ang % 360.0
                break
            if math.hypot(le.x_mm - center.x_mm, le.y_mm - center.y_mm) < 25.0:
                leaf_ang = math.degrees(math.atan2(ls.y_mm - le.y_mm, ls.x_mm - le.x_mm)) % 360.0
                if abs(((leaf_ang - start_ang + 180.0) % 360.0) - 180.0) < 15.0:
                    swing_dir = "cw"
                    angle_deg = end_ang % 360.0
                break

        doors.append(
            DoorOpening(
                id=f"D-{d_idx}",
                room_id=target_rid,
                hinge=Point2D(x_mm=round(center.x_mm, 2), y_mm=round(center.y_mm, 2)),
                width_mm=round(radius, 2),
                angle_deg=round(angle_deg, 2),
                swing_dir=swing_dir,
            )
        )

    # Build WindowOpenings
    windows: list[WindowOpening] = []
    for win_idx, (p0, p1) in enumerate(window_lines, start=1):
        mid = Point((p0.x_mm + p1.x_mm) / 2.0, (p0.y_mm + p1.y_mm) / 2.0)
        target_rid = _find_containing_room(mid, room_poly_pairs) or rooms[0].id
        windows.append(
            WindowOpening(
                id=f"WIN-{win_idx}",
                room_id=target_rid,
                start=Point2D(x_mm=round(p0.x_mm, 2), y_mm=round(p0.y_mm, 2)),
                end=Point2D(x_mm=round(p1.x_mm, 2), y_mm=round(p1.y_mm, 2)),
            )
        )

    # Distribution Board Anchor
    if db_points:
        # Prefer polygon centroid over text offset when both exist on A-DB
        db_id = "DB1"
        for label, _ in db_points:
            if label and label != "DB1":
                db_id = label
                break
        db_anchor = DistributionBoardAnchor(
            id=db_id,
            location_mm=Point2D(
                x_mm=round(db_points[0][1].x_mm, 2),
                y_mm=round(db_points[0][1].y_mm, 2),
            ),
        )
    else:
        corridor = next((r for r in rooms if r.normalized_type == RoomType.CORRIDOR), rooms[0])
        c_pt = corridor.centroid_mm()
        db_anchor = DistributionBoardAnchor(id="DB1", location_mm=c_pt)
        flags.append(
            ReviewFlag(
                code="DB_ANCHOR_INFERRED",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    f"No DB anchor found on layer A-DB; placed DB1 at {corridor.raw_label} "
                    f"({c_pt.x_mm:.0f}, {c_pt.y_mm:.0f} mm). Confirm DB location on plan."
                ),
            )
        )

    resolved_type = building_type or _infer_building_type(rooms)
    total_area = round(sum(r.area_m2 for r in rooms), 4)

    model = BuildingModel(
        project_name=project_name or dxf_path.stem.replace("_", " ").title(),
        building_type=resolved_type,
        units="mm",
        total_floor_area_m2=total_area,
        rooms=rooms,
        walls=walls,
        doors=doors,
        windows=windows,
        db_anchor=db_anchor,
        pv_capacity_kva=pv_capacity_kva,
        source_file=str(dxf_path),
        source_sha256=sha256,
    )
    return model, flags
