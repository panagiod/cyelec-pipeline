"""Resilient R12-R2018 DXF ingestion: INSERT block explosion, wall polygonization, layer mapping."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal

from ezdxf.entities.dxfgfx import DXFGraphic
from ezdxf.entities.insert import Insert
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.entities.mtext import MText
from ezdxf.entities.polyline import Polyline
from ezdxf.entities.spline import Spline
from ezdxf.entities.text import Text
from ezdxf.filemanagement import readfile as read_dxf
from shapely import set_precision
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, snap, unary_union

from cyelec.ingest.messy_cad_recovery import (
    extract_parallel_double_wall_lines,
    score_room_detection_confidence,
    strip_mtext_formatting,
)
from cyelec.ingest.plan_splitter import split_side_by_side_plans
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
    DxfLayerInfo,
    DxfLayerMapping,
    FloorUnderlayRef,
    FurnitureItem,
    PlumbingFixture,
    Point2D,
    Room,
    RoomType,
    SubBoardSpec,
    WallSegment,
    WindowOpening,
)
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.standards_loader import StandardsBundle, load_standards, normalize_label

_FurnitureKind = Literal[
    "bed",
    "tv",
    "sofa",
    "wardrobe",
    "dining_table",
    "kitchen_counter",
    "stove",
    "fridge",
]

_FURNITURE_TOKENS: tuple[tuple[_FurnitureKind, tuple[str, ...]], ...] = (
    ("bed", ("BED", "KING", "QUEEN", "ΚΡΕΒΑΤ")),
    ("tv", ("TV", "TELEVISION", "MEDIA", "ΤΗΛΕΟΡ")),
    ("sofa", ("SOFA", "COUCH", "LOUNGE_SEAT", "ΚΑΝΑΠ")),
    ("wardrobe", ("WARDROBE", "CLOSET", "CUPBOARD", "ΝΤΟΥΛΑΠ")),
    ("dining_table", ("DINING", "TABLE", "ΤΡΑΠΕΖ")),
    ("stove", ("STOVE", "COOKER", "COOKTOP", "CKTOP", "HOB", "RANGE", "ΚΟΥΖΙΝΑ_ΕΣΤ")),
    ("fridge", ("FRIDGE", "REFRIG", "SNGREF", "DBLREF", "FREEZER", "ΨΥΓΕΙ")),
    ("kitchen_counter", ("COUNTER", "ISLAND", "WORKTOP", "CABINET", "ΠΑΓΚ")),
)

# Short Cyprus/Greek architectural appliance callout letters (Ψ=fridge, ΠΛ=washer/dishwasher)
_APPLIANCE_CALLOUT_TOKENS: frozenset[str] = frozenset(
    {"ψ", "πλ", "θ", "η/σ", "wm", "dw", "w/m", "d/w"}
)
_FRIDGE_CALLOUT_TOKENS: frozenset[str] = frozenset({"ψ", "ψυγ", "fr"})


def _classify_furniture_hint(layer: str, block_hint: str | None) -> _FurnitureKind | None:
    """Detect architectural furniture/joinery blocks from block name or furniture layer."""
    combined = f"{layer} {block_hint or ''}".upper()
    if not combined.strip():
        return None
    for fkind, tokens in _FURNITURE_TOKENS:
        if any(tok in combined for tok in tokens):
            return fkind
    return None


_INSUNITS_TO_MM: dict[int, float] = {
    1: 25.4,  # Inches
    2: 304.8,  # Feet
    4: 1.0,  # Millimeters
    5: 10.0,  # Centimeters
    6: 1000.0,  # Meters
}

_ROOM_LAYER_TOKENS = ("A-ROOM", "ROOM", "SPACE", "AREA", "ZONE")
_WALL_LAYER_TOKENS = ("A-WALL", "WALL", "ΤΟΙΧ")
_DOOR_LAYER_TOKENS = ("A-DOOR", "DOOR", "OPENING", "ΠΟΡΤ")
_WIND_LAYER_TOKENS = ("A-WIND", "WIND", "GLAZ", "ΠΑΡΑΘ")
_FIXT_LAYER_TOKENS = ("A-FIXT", "FIXT", "SANIT", "PLUMB", "BATH", "ΕΙΔΗ")
_TEXT_LAYER_TOKENS = ("A-TEXT", "TEXT", "LABEL", "ANNO", "NOTE", "ROOM-TXT", "NAME", "IDEN")
_DB_LAYER_TOKENS = ("A-DB", "E-DB", "E-BOARD", "BOARD", "DB", "PANEL", "ΠΙΝΑΚ")

_LayerRole = Literal["rooms", "walls", "doors", "windows", "fixtures", "text", "db", "unknown"]


def _layer_matches(layer: str, tokens: tuple[str, ...]) -> bool:
    up = layer.upper()
    return any(tok in up for tok in tokens)


def _infer_layer_role(layer: str) -> _LayerRole:
    if _layer_matches(layer, _ROOM_LAYER_TOKENS):
        return "rooms"
    if _layer_matches(layer, _WALL_LAYER_TOKENS):
        return "walls"
    if _layer_matches(layer, _DOOR_LAYER_TOKENS):
        return "doors"
    if _layer_matches(layer, _WIND_LAYER_TOKENS):
        return "windows"
    if _layer_matches(layer, _FIXT_LAYER_TOKENS):
        return "fixtures"
    if _layer_matches(layer, _DB_LAYER_TOKENS):
        return "db"
    if _layer_matches(layer, _TEXT_LAYER_TOKENS):
        return "text"
    return "unknown"


def _role_matches(
    layer: str,
    role: _LayerRole,
    mapping: DxfLayerMapping | None,
) -> bool:
    """Match a layer name using explicit DxfLayerMapping when provided, else token heuristics."""
    if mapping is not None:
        explicit: list[str] = getattr(mapping, role, [])
        if explicit:
            norm = layer.strip().casefold()
            return any(norm == item.strip().casefold() for item in explicit)

    token_map: dict[str, tuple[str, ...]] = {
        "rooms": _ROOM_LAYER_TOKENS,
        "walls": _WALL_LAYER_TOKENS,
        "doors": _DOOR_LAYER_TOKENS,
        "windows": _WIND_LAYER_TOKENS,
        "fixtures": _FIXT_LAYER_TOKENS,
        "text": _TEXT_LAYER_TOKENS,
        "db": _DB_LAYER_TOKENS,
    }
    tokens = token_map.get(role)
    return _layer_matches(layer, tokens) if tokens else False


def _iter_resolved_entities(
    entities: list[DXFGraphic],
    *,
    parent_layer: str | None = None,
    block_hint: str | None = None,
    depth: int = 0,
    max_depth: int = 4,
) -> Iterator[tuple[DXFGraphic, str, str | None]]:
    """Yield primitives, recursively exploding AutoCAD INSERT blocks with Layer-0 inheritance."""
    for ent in entities:
        raw_layer = str(ent.dxf.layer)
        eff_layer = (
            parent_layer if (parent_layer is not None and raw_layer in ("0", "")) else raw_layer
        )
        if isinstance(ent, Insert) and depth < max_depth:
            bname = str(ent.dxf.name)
            try:
                virtual = list(ent.virtual_entities())
            except Exception:
                continue
            yield from _iter_resolved_entities(
                virtual,
                parent_layer=eff_layer,
                block_hint=bname,
                depth=depth + 1,
                max_depth=max_depth,
            )
        else:
            yield ent, eff_layer, block_hint


def inspect_dxf_layers(dxf_path: Path) -> list[DxfLayerInfo]:
    """Inspect all layers in a DXF file with entity counts and inferred architectural roles."""
    if not dxf_path.is_file():
        raise FileNotFoundError(f"DXF file not found: {dxf_path}")
    doc = read_dxf(str(dxf_path))
    msp = doc.modelspace()

    counts_by_layer: dict[str, Counter[str]] = defaultdict(Counter)
    for ent, eff_layer, _hint in _iter_resolved_entities(list(msp)):
        counts_by_layer[eff_layer][ent.dxftype()] += 1

    infos: list[DxfLayerInfo] = []
    for layer_name in sorted(counts_by_layer):
        type_counts = dict(sorted(counts_by_layer[layer_name].items()))
        infos.append(
            DxfLayerInfo(
                layer_name=layer_name,
                entity_count=sum(type_counts.values()),
                entity_types=type_counts,
                inferred_role=_infer_layer_role(layer_name),
            )
        )
    return infos


def _flatten_spline_xy(entity: Spline, scale: float) -> list[tuple[float, float]]:
    """Flatten a DXF SPLINE entity into scaled 2D vertices (mm)."""
    try:
        dist = max(0.02, 50.0 / max(scale, 1e-6))
        return [(float(v.x) * scale, float(v.y) * scale) for v in entity.flattening(dist)]
    except Exception:
        return [(float(p[0]) * scale, float(p[1]) * scale) for p in entity.control_points]


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
    elif isinstance(entity, Spline):
        raw_s = _flatten_spline_xy(entity, scale)
        if len(raw_s) < 3:
            return None
        is_closed = bool(entity.closed) or (
            math.hypot(raw_s[0][0] - raw_s[-1][0], raw_s[0][1] - raw_s[-1][1]) < 25.0
        )
        if not is_closed:
            return None
        pts = raw_s
    else:
        return None

    if len(pts) >= 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-3:
        pts = pts[:-1]
    return pts if len(pts) >= 3 else None


def _extract_text_item(entity: DXFGraphic, scale: float) -> tuple[str, tuple[float, float]] | None:
    if isinstance(entity, MText):
        raw = str(entity.plain_text()).strip()
        text = strip_mtext_formatting(raw) or raw
        ins = entity.dxf.insert
        return (text, (float(ins.x) * scale, float(ins.y) * scale)) if text else None
    if isinstance(entity, Text):
        raw = str(entity.dxf.text).strip()
        text = strip_mtext_formatting(raw) or raw
        ins = entity.dxf.insert
        return (text, (float(ins.x) * scale, float(ins.y) * scale)) if text else None
    return None


def _infer_unit_scale(
    insunits: int,
    raw_extents_max: float,
    flags: list[ReviewFlag],
    *,
    raw_door_arc_radii: list[float] | None = None,
) -> float:
    radii = raw_door_arc_radii or []
    median_arc = sorted(radii)[len(radii) // 2] if radii else 0.0

    if insunits in _INSUNITS_TO_MM:
        declared_scale = _INSUNITS_TO_MM[insunits]
        # Sanity check: real architectural buildings span >= 3000 mm (3.0 m).
        # AutoCAD files often leave $INSUNITS=4 (mm) even when drawn in inches, cm, or metres.
        if declared_scale == 1.0 and 0.0 < raw_extents_max < 2500.0:
            if raw_extents_max < 100.0:
                scale, guessed = 1000.0, "metres"
            elif 18.0 <= median_arc <= 42.0:
                scale, guessed = 25.4, "inches"
            else:
                scale, guessed = 10.0, "centimetres"
            flags.append(
                ReviewFlag(
                    code="DXF_SCALE_AUTO_CORRECTED",
                    severity=ReviewSeverity.WARNING,
                    category="geometry",
                    message=(
                        f"DXF declared $INSUNITS={insunits} (mm) but building span is only "
                        f"{raw_extents_max:.1f} units (median door arc {median_arc:.1f}); "
                        f"auto-corrected to {guessed} ({scale:g} mm/unit). Verify drawing scale."
                    ),
                )
            )
            return scale
        return declared_scale

    if raw_extents_max <= 0.0:
        return 1.0
    if raw_extents_max < 100.0:
        scale, guessed = 1000.0, "metres"
    elif 18.0 <= median_arc <= 42.0 and raw_extents_max < 2500.0:
        scale, guessed = 25.4, "inches"
    elif raw_extents_max < 2500.0:
        scale, guessed = 10.0, "centimetres"
    else:
        scale, guessed = 1.0, "millimetres"
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
        if any(k in norm for k in ("bathtub", "bath", "μπανιερα", "mpaniera")):
            return "bathtub"
        if any(k in norm for k in ("shower", "ντους", "ντουζιερα", "ntoys", "ntous")):
            return "shower_tray"
        if any(k in norm for k in ("basin", "washbasin", "νιπτηρ", "niptir")):
            return "basin"
        if any(k in norm for k in ("sink", "νεροχυτ", "neroxyt")):
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
    types = [r.normalized_type for r in rooms]
    if any(t in (RoomType.WAREHOUSE, RoomType.PLANT_ROOM) for t in types):
        return BuildingType.WORKSHOP
    if any(t == RoomType.RETAIL_FLOOR for t in types):
        return BuildingType.SHOP
    if any(t in (RoomType.OPEN_OFFICE, RoomType.MEETING_ROOM, RoomType.SERVER_ROOM) for t in types):
        return BuildingType.OFFICE

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


def _extract_wall_cavities(
    lines: list[LineString],
    *,
    buffer_mm: float = 140.0,
    subtract_thin_mm: float = 0.0,
) -> list[Polygon]:
    """Morphological wall-cavity extractor for double-line walls and doorless partition gaps."""
    if not lines:
        return []
    merged_lines = unary_union(lines)
    jstyle = "bevel" if len(lines) > 250 else "mitre"
    wall_band = merged_lines.buffer(buffer_mm, quad_segs=2, join_style=jstyle)
    wall_thin = (
        merged_lines.buffer(subtract_thin_mm, quad_segs=2, join_style=jstyle)
        if subtract_thin_mm > 0.0 and len(lines) <= 600
        else None
    )
    geoms = list(wall_band.geoms) if hasattr(wall_band, "geoms") else [wall_band]
    cavities: list[Polygon] = []
    for geom in geoms:
        if not isinstance(geom, Polygon):
            continue
        for interior in geom.interiors:
            hole = Polygon(interior)
            expanded = hole.buffer(max(10.0, buffer_mm - 20.0), quad_segs=2, join_style=jstyle)
            if wall_thin is not None:
                expanded = expanded.difference(wall_thin)
                if hasattr(expanded, "geoms"):
                    poly_parts = [g for g in expanded.geoms if isinstance(g, Polygon)]
                    if not poly_parts:
                        continue
                    expanded = max(poly_parts, key=lambda g: g.area)
            if isinstance(expanded, Polygon) and expanded.is_valid:
                simplified = expanded.simplify(25.0, preserve_topology=True)
                if (
                    isinstance(simplified, Polygon)
                    and simplified.is_valid
                    and 1.2e6 <= simplified.area <= 5e8
                ):
                    cavities.append(simplified)
    return cavities


def _polygonize_rooms_from_walls(
    wall_lines: list[tuple[Point2D, Point2D]],
    door_arcs: list[tuple[Point2D, float, float, float]],
    window_lines: list[tuple[Point2D, Point2D]],
    door_leaves: list[tuple[Point2D, Point2D]] | None = None,
    *,
    flags: list[ReviewFlag] | None = None,
) -> list[Polygon]:
    """Derive closed room polygons from wall + doorway + window segments when A-ROOM is absent."""
    lines: list[LineString] = []
    for p0, p1 in wall_lines:
        if math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm) >= 10.0:
            lines.append(LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)]))
    for p0, p1 in window_lines:
        if math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm) >= 10.0:
            lines.append(LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)]))
    for p0, p1 in door_leaves or []:
        if math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm) >= 10.0:
            lines.append(LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)]))
    # Seal doorway wall openings along both start and end rays of each swing arc
    for center, radius, start_ang, end_ang in door_arcs:
        for ang in (start_ang, end_ang):
            rad = math.radians(ang)
            end_x = center.x_mm + radius * math.cos(rad)
            end_y = center.y_mm + radius * math.sin(rad)
            lines.append(LineString([(center.x_mm, center.y_mm), (end_x, end_y)]))

    if not lines:
        return []

    # Guard against dense raw PDF-to-DXF vector clouds (>600 segments) where O(V^2) GEOS snap stalls
    dense_raw = len(lines) > 600
    if dense_raw:
        orig_count = len(lines)
        long_lines = [ln for ln in lines if ln.length >= 150.0]
        if len(long_lines) >= 30:
            lines = long_lines
        if len(lines) > 1200:
            lines.sort(key=lambda ln: ln.length, reverse=True)
            lines = lines[:1200]
        if flags is not None and len(lines) < orig_count:
            flags.append(
                ReviewFlag(
                    code="RAW_DENSE_GEOMETRY_SIMPLIFIED",
                    severity=ReviewSeverity.WARNING,
                    category="geometry",
                    message=(
                        f"Dense unlayered CAD/PDF vector input ({orig_count} segments) filtered "
                        f"to {len(lines)} structural wall candidates (>=150 mm) to prevent "
                        "polygonization timeout. Prefer clean layered AutoCAD DXF/DWG input."
                    ),
                )
            )

    merged = unary_union(lines)
    if dense_raw:
        # O(N log N) fixed-precision grid noding instead of O(V^2) GEOS snap
        noded = unary_union(set_precision(merged, 25.0))
    else:
        # Snap tiny drafting tolerances (<= 80 mm) so nearly touching wall corners close cleanly
        snapped = snap(merged, merged, 80.0)
        noded = unary_union(snapped)

    raw_polys = [
        p for p in polygonize(noded) if isinstance(p, Polygon) and 1_200_000.0 <= p.area <= 5e8
    ]
    if len(raw_polys) < 2 and len(lines) <= 800:
        cavities = _extract_wall_cavities(lines, buffer_mm=140.0)
        if len(cavities) > len(raw_polys):
            raw_polys = cavities

    # Morphological doorway-gap closing (<= 960 mm door openings) when drawing has no door
    # geometry OR walls severely under-segmented due to open/unbridged interior doorways.
    has_doors = bool(door_arcs or door_leaves)
    if len(lines) <= 800 and ((not has_doors) or (len(raw_polys) <= 3 and len(wall_lines) >= 20)):
        bridged = _extract_wall_cavities(lines, buffer_mm=480.0, subtract_thin_mm=80.0)
        raw_area = sum(p.area for p in raw_polys)
        bridged_area = sum(p.area for p in bridged)
        if len(bridged) > len(raw_polys) and (raw_area <= 0.0 or bridged_area >= 0.70 * raw_area):
            raw_polys = bridged
            if flags is not None:
                flags.append(
                    ReviewFlag(
                        code="DOORWAY_GAPS_MORPHOLOGICALLY_BRIDGED",
                        severity=ReviewSeverity.INFO,
                        category="geometry",
                        message=(
                            f"Bridged open/doorless partition gaps (<= 960 mm) morphologically "
                            f"to separate {len(bridged)} enclosed room polygon(s). "
                            "Verify room boundaries."
                        ),
                    )
                )

    if not raw_polys:
        return []

    # Guard against O(P^2) containment checks on uncleaned raw PDF/hatch vector dumps
    if len(raw_polys) > 150:
        raw_polys.sort(key=lambda p: p.area, reverse=True)
        raw_polys = raw_polys[:150]

    # Drop any outer shell polygon that encloses 2+ smaller interior rooms
    filtered: list[Polygon] = []
    for i, cand in enumerate(raw_polys):
        contained = sum(
            1
            for j, other in enumerate(raw_polys)
            if i != j
            and cand.area > other.area * 1.15
            and cand.contains(other.representative_point())
        )
        if contained < 2:
            filtered.append(cand)
    return filtered


def ingest_dxf(
    dxf_path: Path,
    *,
    standards: StandardsBundle | None = None,
    standards_dir: Path | None = None,
    project_name: str | None = None,
    building_type: BuildingType | None = None,
    pv_capacity_kva: float = 0.0,
    tier2_fallback: RoomClassifierFallback | None = None,
    layer_mapping: DxfLayerMapping | None = None,
    auto_split_multi_plan: bool = True,
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

    # Detect unresolved external XREF attachments common in Cyprus architect DWGs
    xref_names = sorted(
        blk.name
        for blk in doc.blocks
        if blk.block is not None
        and (
            getattr(blk.block, "is_xref", False)
            or getattr(blk.block, "is_xref_overlay", False)
            or bool(str(blk.block.dxf.get("xref_path", "")).strip())
        )
    )
    if xref_names:
        flags.append(
            ReviewFlag(
                code="DXF_UNRESOLVED_XREFS",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    f"Input CAD references {len(xref_names)} external XREF block(s) "
                    f"({', '.join(xref_names[:5])}). Bind XREFs in AutoCAD (XREF -> Bind -> "
                    "Insert) if architectural walls, grids, or sanitary fixtures are missing."
                ),
                requires_etek_signoff=True,
            )
        )

    msp = doc.modelspace()
    resolved_entities = list(_iter_resolved_entities(list(msp)))

    # First pass: compute raw coordinate span & door arc radii for unit/scale sanity checks
    xs: list[float] = []
    ys: list[float] = []
    raw_arc_radii: list[float] = []
    for ent, layer_name, _hint in resolved_entities:
        t = ent.dxftype()
        if t == "LINE":
            xs.extend([float(ent.dxf.start.x), float(ent.dxf.end.x)])
            ys.extend([float(ent.dxf.start.y), float(ent.dxf.end.y)])
        elif isinstance(ent, LWPolyline):
            for px, py in ent.get_points("xy"):
                xs.append(float(px))
                ys.append(float(py))
        elif isinstance(ent, Spline):
            for cp in ent.control_points:
                xs.append(float(cp[0]))
                ys.append(float(cp[1]))
        elif t == "ARC" and _role_matches(layer_name, "doors", layer_mapping):
            raw_arc_radii.append(float(ent.dxf.radius))
    span = max((max(xs) - min(xs)) if xs else 0.0, (max(ys) - min(ys)) if ys else 0.0)
    insunits = int(doc.header.get("$INSUNITS", 0))
    scale = _infer_unit_scale(insunits, span, flags, raw_door_arc_radii=raw_arc_radii)

    room_raw_polys: list[Polygon] = []
    text_labels: list[tuple[str, Point, str]] = []
    leader_arrows: list[tuple[Point, Point]] = []
    fixture_polys: list[tuple[Polygon, str | None]] = []
    fixture_texts: list[tuple[str, Point]] = []
    furniture_polys: list[tuple[Polygon, _FurnitureKind]] = []
    untyped_furniture_polys: list[Polygon] = []
    furniture_texts: list[tuple[str, Point]] = []
    wall_lines: list[tuple[Point2D, Point2D]] = []
    layer0_lines: list[tuple[Point2D, Point2D]] = []
    door_arcs: list[tuple[Point2D, float, float, float]] = []
    fallback_door_arcs: list[tuple[Point2D, float, float, float]] = []
    door_leaves: list[tuple[Point2D, Point2D]] = []
    window_lines: list[tuple[Point2D, Point2D]] = []
    db_points: list[tuple[str, Point2D]] = []

    def _maybe_record_fallback_door_arc(arc_ent: DXFGraphic) -> None:
        r_mm = float(arc_ent.dxf.radius) * scale
        if not (600.0 <= r_mm <= 1250.0):
            return
        sa = float(arc_ent.dxf.start_angle)
        ea = float(arc_ent.dxf.end_angle)
        span = (ea - sa) % 360.0
        if 75.0 <= span <= 105.0:
            fallback_door_arcs.append(
                (
                    Point2D(
                        x_mm=float(arc_ent.dxf.center.x) * scale,
                        y_mm=float(arc_ent.dxf.center.y) * scale,
                    ),
                    r_mm,
                    sa,
                    ea,
                )
            )

    # Extract room label attributes from INSERT blocks (e.g. RM-TAG with NAME='Guest bedroom')
    for raw_ent in msp:
        if raw_ent.dxftype() == "INSERT":
            raw_layer = str(getattr(raw_ent.dxf, "layer", "0") or "0")
            if _role_matches(raw_layer, "text", layer_mapping):
                attribs = getattr(raw_ent, "attribs", None) or []
                for att in attribs:
                    tag_up = str(getattr(att.dxf, "tag", "") or "").upper()
                    val = str(getattr(att.dxf, "text", "") or "").strip()
                    if val and tag_up in ("NAME", "ROOM", "ROOM_NAME", "LABEL", "TITLE"):
                        ins = raw_ent.dxf.insert
                        text_labels.append(
                            (val, Point(float(ins.x) * scale, float(ins.y) * scale), raw_layer)
                        )

    for ent, layer, block_hint in resolved_entities:
        dtype = ent.dxftype()
        hint_up = (block_hint or "").upper()
        layer_up = layer.upper()

        # DXF LEADER / MULTILEADER arrows pointing external room labels into narrow polygons
        if dtype == "LEADER":
            verts = list(getattr(ent, "vertices", []) or [])
            if len(verts) >= 2:
                tip_v, tail_v = verts[0], verts[-1]
                leader_arrows.append(
                    (
                        Point(float(tip_v[0]) * scale, float(tip_v[1]) * scale),
                        Point(float(tail_v[0]) * scale, float(tail_v[1]) * scale),
                    )
                )
            continue
        if dtype == "MULTILEADER":
            ctx = getattr(ent, "context", None)
            mtext_obj = getattr(ctx, "mtext", None) if ctx is not None else None
            ml_text = str(getattr(mtext_obj, "default_content", "") or "").strip()
            for ld in getattr(ctx, "leaders", []) or []:
                for line in getattr(ld, "lines", []) or []:
                    verts = list(getattr(line, "vertices", []) or [])
                    if verts:
                        tip_v = verts[0]
                        tail_v = getattr(ld, "last_leader_point", None) or verts[-1]
                        tip_pt = Point(float(tip_v[0]) * scale, float(tip_v[1]) * scale)
                        tail_pt = Point(float(tail_v[0]) * scale, float(tail_v[1]) * scale)
                        leader_arrows.append((tip_pt, tail_pt))
                        if ml_text:
                            text_labels.append((ml_text, tail_pt, layer))
            continue

        # Room boundary polygons
        if _role_matches(layer, "rooms", layer_mapping):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                poly = Polygon(coords)
                if not poly.is_valid:
                    poly = poly.buffer(0.0)
                if isinstance(poly, Polygon) and poly.area >= 500_000.0:
                    room_raw_polys.append(poly)

        # Wall segments (LINE, LWPOLYLINE, and flattened curved SPLINE walls)
        elif _role_matches(layer, "walls", layer_mapping):
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
            elif isinstance(ent, Spline):
                s_pts = _flatten_spline_xy(ent, scale)
                for a, b in zip(s_pts, s_pts[1:], strict=False):
                    wall_lines.append(
                        (Point2D(x_mm=a[0], y_mm=a[1]), Point2D(x_mm=b[0], y_mm=b[1]))
                    )
                if ent.closed and len(s_pts) >= 3:
                    wall_lines.append(
                        (
                            Point2D(x_mm=s_pts[-1][0], y_mm=s_pts[-1][1]),
                            Point2D(x_mm=s_pts[0][0], y_mm=s_pts[0][1]),
                        )
                    )

        # Doors (on door layer OR exploded from a DOOR block)
        elif _role_matches(layer, "doors", layer_mapping) or "DOOR" in hint_up:
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

        # Windows (also capture 90-deg door swings when architects place doors on Window layer)
        elif _role_matches(layer, "windows", layer_mapping):
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
            elif dtype == "ARC":
                _maybe_record_fallback_door_arc(ent)

        # Distribution board anchor (layer OR DB block name)
        elif _role_matches(layer, "db", layer_mapping) or any(
            tok in hint_up for tok in ("DB1", "MDB", "DIST_BOARD", "PANEL")
        ):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                c = Polygon(coords).centroid
                db_points.append((block_hint or "DB1", Point2D(x_mm=float(c.x), y_mm=float(c.y))))
            else:
                txt = _extract_text_item(ent, scale)
                if txt is not None:
                    db_points.append(
                        (txt[0].strip() or "DB1", Point2D(x_mm=txt[1][0], y_mm=txt[1][1]))
                    )

        # Plumbing fixtures (layer OR exploded from BATH/SHOWER/WC/SINK/BASIN block)
        elif _role_matches(layer, "fixtures", layer_mapping) or any(
            tok in hint_up for tok in ("BATH", "SHOWER", "BASIN", "SINK", "WC", "TOILET")
        ):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                fpoly = Polygon(coords)
                if fpoly.is_valid and fpoly.area >= 40_000.0:
                    fixture_polys.append((fpoly, block_hint))
            else:
                txt = _extract_text_item(ent, scale)
                if txt is not None:
                    fixture_texts.append((txt[0], Point(txt[1])))

        # Architectural furniture & joinery blocks (BED/SOFA/TV/WARDROBE/DINING/STOVE/FRIDGE)
        elif (furn_kind := _classify_furniture_hint(layer, block_hint)) is not None:
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                furn_poly = Polygon(coords)
                if not furn_poly.is_valid:
                    furn_poly = furn_poly.buffer(0.0)
                if isinstance(furn_poly, Polygon) and 80_000.0 <= furn_poly.area <= 15_000_000.0:
                    furniture_polys.append((furn_poly, furn_kind))
            else:
                txt = _extract_text_item(ent, scale)
                if txt is not None:
                    furniture_texts.append((txt[0], Point(txt[1])))
        elif any(tok in layer_up for tok in ("FURN", "FURNITURE", "JOINERY", "EQUIP")):
            coords = _extract_poly_coords(ent, scale)
            if coords is not None:
                furn_poly = Polygon(coords)
                if not furn_poly.is_valid:
                    furn_poly = furn_poly.buffer(0.0)
                if isinstance(furn_poly, Polygon) and 80_000.0 <= furn_poly.area <= 15_000_000.0:
                    untyped_furniture_polys.append(furn_poly)
            else:
                txt = _extract_text_item(ent, scale)
                if txt is not None:
                    furniture_texts.append((txt[0], Point(txt[1])))

        # Layer "0" fallback lines & 90-deg door arcs (used only when dedicated layers are absent)
        elif layer == "0" and not block_hint:
            if dtype == "LINE":
                layer0_lines.append(
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
            elif dtype == "ARC":
                _maybe_record_fallback_door_arc(ent)
            elif isinstance(ent, LWPolyline):
                pts = [(float(x) * scale, float(y) * scale) for x, y in ent.get_points("xy")]
                for a, b in zip(pts, pts[1:], strict=False):
                    layer0_lines.append(
                        (Point2D(x_mm=a[0], y_mm=a[1]), Point2D(x_mm=b[0], y_mm=b[1]))
                    )
                if ent.closed and len(pts) >= 3:
                    layer0_lines.append(
                        (
                            Point2D(x_mm=pts[-1][0], y_mm=pts[-1][1]),
                            Point2D(x_mm=pts[0][0], y_mm=pts[0][1]),
                        )
                    )

        # Architectural text labels (exclude furniture layer labels & short Greek appliance letters)
        is_furn_layer = any(tok in layer_up for tok in ("FURN", "FURNITURE", "JOINERY"))
        if (
            _role_matches(layer, "text", layer_mapping)
            or (
                dtype in ("TEXT", "MTEXT")
                and not _role_matches(layer, "fixtures", layer_mapping)
                and not _role_matches(layer, "db", layer_mapping)
                and not is_furn_layer
            )
        ):
            txt = _extract_text_item(ent, scale)
            if txt is not None:
                norm_tok = normalize_label(clean_room_label(txt[0]))
                if norm_tok in _APPLIANCE_CALLOUT_TOKENS:
                    if norm_tok in _FRIDGE_CALLOUT_TOKENS:
                        fx, fy = txt[1]
                        furniture_polys.append(
                            (
                                Polygon(
                                    [
                                        (fx - 300.0, fy - 300.0),
                                        (fx + 300.0, fy - 300.0),
                                        (fx + 300.0, fy + 300.0),
                                        (fx - 300.0, fy + 300.0),
                                    ]
                                ),
                                "fridge",
                            )
                        )
                else:
                    text_labels.append((txt[0], Point(txt[1]), layer))

    # Resolve untyped furniture polylines (e.g. A-FURN rectangle + 'BED'/'DINING TABLE' text)
    for fpoly in untyped_furniture_polys:
        for ftxt, fpt in furniture_texts:
            if fpoly.contains(fpt) or fpoly.distance(fpt) < 400.0:
                fk = _classify_furniture_hint("", ftxt)
                if fk is not None:
                    furniture_polys.append((fpoly, fk))
                    break

    # Fallback: drawings with walls drawn purely on layer "0" (legacy homeowner/AutoCAD plans)
    if not room_raw_polys and not wall_lines and layer0_lines:
        if len(layer0_lines) >= 80:
            paired_walls = extract_parallel_double_wall_lines(layer0_lines)
            wall_lines = paired_walls if len(paired_walls) >= 16 else layer0_lines
        else:
            wall_lines = layer0_lines
        flags.append(
            ReviewFlag(
                code="WALLS_INFERRED_FROM_LAYER_0",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    f"No dedicated wall or room layer found; inferred {len(wall_lines)} wall "
                    "segment(s) from default layer '0'. Verify room geometry or supply "
                    "DxfLayerMapping."
                ),
            )
        )

    if not door_arcs and fallback_door_arcs:
        door_arcs = fallback_door_arcs

    # Wall-to-room polygonization fallback OR hybrid merge for partial A-ROOM drawings
    if not room_raw_polys and wall_lines:
        room_raw_polys = _polygonize_rooms_from_walls(
            wall_lines, door_arcs, window_lines, door_leaves=door_leaves, flags=flags
        )
        if room_raw_polys:
            flags.append(
                ReviewFlag(
                    code="ROOMS_POLYGONIZED_FROM_WALLS",
                    severity=ReviewSeverity.INFO,
                    category="geometry",
                    message=(
                        f"No closed room polylines found; automatically derived "
                        f"{len(room_raw_polys)} room polygon(s) from wall/door geometry. "
                        "Verify room boundaries."
                    ),
                )
            )
    elif room_raw_polys and wall_lines:
        # Hybrid merge: recover enclosed rooms that lack explicit A-ROOM polylines
        wall_derived = _polygonize_rooms_from_walls(
            wall_lines, door_arcs, window_lines, door_leaves=door_leaves, flags=flags
        )
        existing_union = unary_union(room_raw_polys)
        added_hybrid = 0
        for cand in wall_derived:
            if cand.area < 1_000_000.0:
                continue
            # Reject any candidate that overlaps existing rooms (including outer floor shells!)
            if (cand.intersection(existing_union).area / max(cand.area, 1.0)) >= 0.25:
                continue
            room_raw_polys.append(cand)
            existing_union = unary_union([existing_union, cand])
            added_hybrid += 1
        if added_hybrid > 0:
            flags.append(
                ReviewFlag(
                    code="ROOMS_HYBRID_AUGMENTED_FROM_WALLS",
                    severity=ReviewSeverity.INFO,
                    category="geometry",
                    message=(
                        f"Augmented {len(room_raw_polys) - added_hybrid} explicit room "
                        f"polyline(s) with {added_hybrid} additional wall-enclosed room(s) "
                        "lacking A-ROOM outlines."
                    ),
                )
            )

    # Drop any outer building/floor shell polygon enclosing 2+ smaller interior rooms
    if len(room_raw_polys) >= 3:
        deduped_rooms: list[Polygon] = []
        for i, cand in enumerate(room_raw_polys):
            contained = sum(
                1
                for j, other in enumerate(room_raw_polys)
                if i != j
                and cand.area > other.area * 1.15
                and cand.contains(other.representative_point())
            )
            if contained < 2:
                deduped_rooms.append(cand)
        if deduped_rooms:
            room_raw_polys = deduped_rooms

    if not room_raw_polys:
        raise ValueError(
            f"No closed room polygons (>= 0.5 m²) or polygonizable wall loops found in "
            f"DXF '{dxf_path}'. Use DxfLayerMapping to map custom architect layers."
        )

    # Sort rooms deterministically (top-to-bottom, left-to-right by centroid)
    room_raw_polys.sort(key=lambda p: (-round(p.centroid.y, 1), round(p.centroid.x, 1)))

    rooms: list[Room] = []
    room_poly_pairs: list[tuple[str, Polygon]] = []
    room_flags_by_id: dict[str, list[ReviewFlag]] = defaultdict(list)
    tmpl_std = std.room_templates

    # Pre-index text labels that sit outside ALL room polygons (for LEADER & veranda fallback)
    outside_text_indices: list[int] = [
        ti
        for ti, (_t, pt, _l) in enumerate(text_labels)
        if not any(p.contains(pt) or p.touches(pt) for p in room_raw_polys)
    ]
    leader_texts_by_room_idx: dict[int, list[str]] = defaultdict(list)
    consumed_outside_text: set[int] = set()
    for tip_pt, tail_pt in leader_arrows:
        pointed_idx = next(
            (
                i
                for i, p in enumerate(room_raw_polys)
                if p.contains(tip_pt) or p.distance(tip_pt) <= 120.0
            ),
            None,
        )
        if pointed_idx is None:
            continue
        best_ti: int | None = None
        best_d = 2500.0
        for ti in outside_text_indices:
            if ti in consumed_outside_text:
                continue
            d = float(tail_pt.distance(text_labels[ti][1]))
            if d < best_d:
                best_d = d
                best_ti = ti
        if best_ti is not None:
            consumed_outside_text.add(best_ti)
            leader_texts_by_room_idx[pointed_idx].append(text_labels[best_ti][0])

    # Near-boundary fallback for unlabelled outdoor verandas/entrances when leader is plain LINEs
    unlabelled_room_indices = [
        i
        for i, p in enumerate(room_raw_polys)
        if not leader_texts_by_room_idx.get(i)
        and not any(p.contains(pt) or p.touches(pt) for _t, pt, _l in text_labels)
    ]
    for ti in outside_text_indices:
        if ti in consumed_outside_text:
            continue
        txt_val, txt_pt, _ = text_labels[ti]
        cls_probe = classify_room_label(clean_room_label(txt_val), tmpl_std)
        if cls_probe.confidence < 0.90 or cls_probe.normalized_type not in (
            RoomType.VERANDA,
            RoomType.CORRIDOR,
        ):
            continue
        best_ri: int | None = None
        best_d = 1800.0
        for ri in unlabelled_room_indices:
            if leader_texts_by_room_idx.get(ri):
                continue
            d = float(room_raw_polys[ri].distance(txt_pt))
            if d < best_d:
                best_d = d
                best_ri = ri
        if best_ri is not None:
            consumed_outside_text.add(ti)
            leader_texts_by_room_idx[best_ri].append(txt_val)

    for idx, poly in enumerate(room_raw_polys, start=1):
        rid = f"R{idx}"
        room_poly_pairs.append((rid, poly))

        inside_texts = [t for t, pt, _ in text_labels if poly.contains(pt) or poly.touches(pt)]
        if not inside_texts and leader_texts_by_room_idx.get(idx - 1):
            inside_texts = list(leader_texts_by_room_idx[idx - 1])
        if inside_texts:
            # Prefer texts matching Tier-1 room aliases over CAD door/window callouts (e.g. '3068')
            best_label = clean_room_label(inside_texts[0])
            best_cls = classify_room_label(
                best_label, tmpl_std, room_id=rid, tier2_fallback=tier2_fallback
            )
            if len(inside_texts) > 1:
                candidates = [
                    (
                        clean_room_label(t),
                        classify_room_label(
                            clean_room_label(t),
                            tmpl_std,
                            room_id=rid,
                            tier2_fallback=tier2_fallback,
                        ),
                    )
                    for t in inside_texts
                ]
                joined_lbl = " ".join(c[0] for c in candidates if c[0] and not c[0][0].isdigit())
                if joined_lbl:
                    candidates.append(
                        (
                            joined_lbl,
                            classify_room_label(
                                joined_lbl,
                                tmpl_std,
                                room_id=rid,
                                tier2_fallback=tier2_fallback,
                            ),
                        )
                    )
                matched_types = {
                    c[1].normalized_type for c in candidates if c[1].confidence >= 0.90
                }
                area_m2_est = float(poly.area) / 1_000_000.0

                if RoomType.KITCHEN in matched_types and RoomType.LIVING_ROOM in matched_types:
                    best_label = " / ".join(
                        dict.fromkeys(
                            c[0]
                            for c in candidates
                            if c[1].confidence >= 0.90 and c[0] != joined_lbl
                        )
                    )
                    kl_cls = classify_room_label(
                        "kitchen/living", tmpl_std, room_id=rid, tier2_fallback=tier2_fallback
                    )
                    best_cls = replace(kl_cls, cleaned_label=best_label or kl_cls.cleaned_label)
                elif (
                    RoomType.UTILITY in matched_types
                    and RoomType.STORAGE in matched_types
                    and RoomType.KITCHEN not in matched_types
                ):
                    best_label, best_cls = next(
                        c for c in candidates if c[1].normalized_type == RoomType.UTILITY
                    )
                elif (
                    area_m2_est > 12.0
                    and (
                        RoomType.MASTER_BEDROOM in matched_types
                        or RoomType.BEDROOM in matched_types
                    )
                    and (RoomType.ENSUITE in matched_types or RoomType.BATHROOM in matched_types)
                ):
                    pref = (
                        RoomType.MASTER_BEDROOM
                        if RoomType.MASTER_BEDROOM in matched_types
                        else RoomType.BEDROOM
                    )
                    best_label, best_cls = next(
                        c for c in candidates if c[1].normalized_type == pref
                    )
                    room_flags_by_id[rid].append(
                        ReviewFlag(
                            code="OPEN_PLAN_MULTI_LABEL_ROOM",
                            severity=ReviewSeverity.INFO,
                            category="geometry",
                            message=(
                                f"Room {rid} ({area_m2_est:.1f} m²) contains multiple labels "
                                f"({', '.join(t.value for t in sorted(matched_types))}); "
                                f"classified as '{pref.value}'. Verify archway/partition."
                            ),
                        )
                    )
                else:
                    # Preserve combined labels when multiple distinct aliases share the same type
                    # (e.g. Greek open-plan 'καθιστικό' + 'τραπεζαρία' -> 'καθιστικό / τραπεζαρία')
                    high_conf_singles = [
                        c
                        for c in candidates
                        if c[1].confidence >= 0.90 and c[0] != joined_lbl
                    ]
                    if (
                        len(matched_types) == 1
                        and len({c[0].lower() for c in high_conf_singles}) >= 2
                        and RoomType.LIVING_ROOM in matched_types
                    ):
                        best_label = " / ".join(dict.fromkeys(c[0] for c in high_conf_singles))
                        best_cls = replace(high_conf_singles[0][1], cleaned_label=best_label)
                    else:
                        candidates.sort(
                            key=lambda item: (item[1].confidence, len(item[1].matched_alias or "")),
                            reverse=True,
                        )
                        best_label, best_cls = candidates[0]
            raw_label = best_label
            cls_res = best_cls
        else:
            raw_label = f"Unnamed Room {idx}"
            room_flags_by_id[rid].append(
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
            room_flags_by_id[rid].append(cls_res.flag)

        rt = tmpl_std.rooms[cls_res.normalized_type]
        ring_coords = list(poly.exterior.coords)[:-1]
        poly_pts = [
            Point2D(x_mm=round(float(x), 2), y_mm=round(float(y), 2)) for x, y in ring_coords
        ]
        area_m2 = round(float(poly.area) / 1_000_000.0, 4)

        used_wall_poly = any(f.code == "ROOMS_POLYGONIZED_FROM_WALLS" for f in flags)
        det_conf = score_room_detection_confidence(
            poly,
            has_matched_text_label=bool(inside_texts),
            from_explicit_room_layer=not used_wall_poly,
            from_pdf_vector=False,
        )
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
                detection_confidence=det_conf,
            )
        )

    # Fail-fast guard: reject uncleaned raw PDF-to-DXF vector soup (layer '0' only, >40 cells,
    # zero readable DXF text labels) instead of routing garbage hatch polygons.
    used_layer0_fallback = any(f.code == "WALLS_INFERRED_FROM_LAYER_0" for f in flags)
    unlabelled_count = sum(1 for r in rooms if r.raw_label.startswith("Unnamed Room"))
    if used_layer0_fallback and len(rooms) > 40 and unlabelled_count == len(rooms):
        raise ValueError(
            f"Input DXF '{dxf_path.name}' appears to be an unlayered raw vector/PDF export "
            f"({len(rooms)} unlabelled cells on layer '0' and 0 readable DXF room text labels). "
            "Export a native layered AutoCAD/BIM DXF/DWG with TEXT/MTEXT room labels (e.g. "
            "A-WALL, A-ROOM, A-DOOR) or supply DxfLayerMapping."
        )

    # Automatic side-by-side / stacked multi-plan cluster splitting & non-floor sheet exclusion
    sub_boards: list[SubBoardSpec] = []
    auto_split_db_anchor: DistributionBoardAnchor | None = None
    excluded_room_ids: set[str] = set()
    room_id_remap: dict[str, str] = {r.id: r.id for r in rooms}
    did_auto_split_floors = False

    if auto_split_multi_plan:
        split_res = split_side_by_side_plans(
            rooms=rooms,
            room_poly_pairs=room_poly_pairs,
            text_labels=text_labels,
            db_points=db_points,
        )
        rooms = split_res.rooms
        room_poly_pairs = split_res.room_poly_pairs
        room_raw_polys = [poly for _rid, poly in room_poly_pairs]
        sub_boards = split_res.sub_boards
        auto_split_db_anchor = split_res.db_anchor
        excluded_room_ids = split_res.excluded_room_ids
        room_id_remap = split_res.room_id_remap
        did_auto_split_floors = split_res.did_split_floors

        if split_res.did_exclude_non_floor and room_raw_polys:
            retained_union = unary_union(room_raw_polys)
            wall_lines = [
                (p0, p1)
                for p0, p1 in wall_lines
                if retained_union.distance(
                    Point((p0.x_mm + p1.x_mm) * 0.5, (p0.y_mm + p1.y_mm) * 0.5)
                )
                <= 1000.0
            ]
            door_arcs = [
                arc
                for arc in door_arcs
                if retained_union.distance(Point(arc[0].x_mm, arc[0].y_mm)) <= 1000.0
            ]
            window_lines = [
                (p0, p1)
                for p0, p1 in window_lines
                if retained_union.distance(
                    Point((p0.x_mm + p1.x_mm) * 0.5, (p0.y_mm + p1.y_mm) * 0.5)
                )
                <= 1000.0
            ]

    for old_rid, r_flags in room_flags_by_id.items():
        if old_rid in excluded_room_ids:
            continue
        new_rid = room_id_remap.get(old_rid, old_rid)
        for rf in r_flags:
            if new_rid != old_rid and f"Room {old_rid} " in rf.message:
                updated_msg = rf.message.replace(f"Room {old_rid} ", f"Room {new_rid} ")
                flags.append(rf.model_copy(update={"message": updated_msg}))
            else:
                flags.append(rf)

    if auto_split_multi_plan:
        flags.extend(split_res.flags)

    rooms_by_id = {r.id: r for r in rooms}

    # Attach plumbing fixtures (using block-name hint or nearby text label)
    for f_idx, (fpoly, f_hint) in enumerate(fixture_polys, start=1):
        target_rid = _find_containing_room(
            fpoly.centroid, room_poly_pairs, max_fallback_dist_mm=500.0
        )
        if target_rid is None:
            continue
        room_obj = rooms_by_id[target_rid]
        matched_label: str | None = f_hint
        if not matched_label:
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

    # P0 Life-Safety Promotion: unlabelled rooms containing bathtub/shower/WC MUST NOT stay STORAGE!
    for room_obj in rooms:
        if (
            room_obj.normalized_type == RoomType.STORAGE
            and room_obj.raw_label.startswith("Unnamed Room")
            and room_obj.fixtures
        ):
            ftypes = {str(fx.fixture_type) for fx in room_obj.fixtures}
            promoted_type: RoomType | None = None
            promoted_lbl: str | None = None
            if "bathtub" in ftypes or "shower_tray" in ftypes:
                promoted_type = RoomType.BATHROOM
                promoted_lbl = "Bathroom (inferred)"
            elif "wc" in ftypes:
                promoted_type = RoomType.WC
                promoted_lbl = "WC (inferred)"
            if promoted_type is not None and promoted_lbl is not None:
                room_obj.normalized_type = promoted_type
                room_obj.is_wet_room = True
                room_obj.raw_label = promoted_lbl
                flags = [
                    f
                    for f in flags
                    if not (
                        f.code == "ROOM_LABEL_MISSING" and f"Room {room_obj.id} " in f.message
                    )
                ]
                flags.append(
                    ReviewFlag(
                        code="WET_ROOM_INFERRED_FROM_FIXTURES",
                        severity=ReviewSeverity.INFO,
                        category="bs7671",
                        message=(
                            f"Unlabelled room {room_obj.id} ({room_obj.area_m2:.2f} m²) contains "
                            f"sanitary fixtures ({', '.join(sorted(ftypes))}); automatically "
                            f"promoted to '{promoted_type.value}' with full BS 7671 Sec. 701 "
                            "wet-zone safety rules."
                        ),
                    )
                )

    # Attach detected furniture/joinery items for furniture-aware electrical placement
    for furn_idx, (furn_poly, furn_kind) in enumerate(furniture_polys, start=1):
        target_rid = _find_containing_room(
            furn_poly.centroid, room_poly_pairs, max_fallback_dist_mm=300.0
        )
        if target_rid is None:
            continue
        room_obj = rooms_by_id[target_rid]
        fc = list(furn_poly.exterior.coords)[:-1]
        if len(fc) < 3:
            continue
        cent = furn_poly.centroid
        room_obj.furniture.append(
            FurnitureItem(
                id=f"FURN-{furn_idx}",
                room_id=target_rid,
                furniture_type=furn_kind,
                polygon_mm=[
                    Point2D(x_mm=round(float(x), 2), y_mm=round(float(y), 2)) for x, y in fc
                ],
                centroid_mm=Point2D(x_mm=round(float(cent.x), 2), y_mm=round(float(cent.y), 2)),
            )
        )

    # Build WallSegments (exact exterior perimeter via room-union boundary)
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
        mid_rad = math.radians((start_ang + ((end_ang - start_ang) % 360.0) / 2.0) % 360.0)
        probe = Point(
            center.x_mm + 0.5 * radius * math.cos(mid_rad),
            center.y_mm + 0.5 * radius * math.sin(mid_rad),
        )
        target_rid = _find_containing_room(probe, room_poly_pairs) or rooms[0].id

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

    # Promote unlabelled compact circulation hubs (>= 3 doors touching boundary, <= 16 m²)
    poly_by_rid = dict(room_poly_pairs)
    for room_obj in rooms:
        if (
            room_obj.normalized_type == RoomType.STORAGE
            and room_obj.raw_label.startswith("Unnamed Room")
            and not room_obj.is_wet_room
            and not room_obj.is_outdoor
            and room_obj.area_m2 <= 16.0
        ):
            rpoly = poly_by_rid.get(room_obj.id)
            if rpoly is None:
                continue
            touching_doors = sum(
                1
                for d in doors
                if d.room_id == room_obj.id
                or float(rpoly.exterior.distance(Point(d.hinge.x_mm, d.hinge.y_mm))) <= 150.0
            )
            if touching_doors >= 3:
                room_obj.normalized_type = RoomType.CORRIDOR
                room_obj.raw_label = "Corridor / Hall (inferred)"
                flags = [
                    f
                    for f in flags
                    if not (
                        f.code == "ROOM_LABEL_MISSING" and f"Room {room_obj.id} " in f.message
                    )
                ]
                flags.append(
                    ReviewFlag(
                        code="CORRIDOR_INFERRED_FROM_DOORS",
                        severity=ReviewSeverity.INFO,
                        category="geometry",
                        message=(
                            f"Unlabelled room {room_obj.id} ({room_obj.area_m2:.2f} m²) serves "
                            f"{touching_doors} doorways; promoted to 'corridor' for 2-way "
                            "circulation switching and DB placement."
                        ),
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
    if auto_split_db_anchor is not None:
        db_anchor = auto_split_db_anchor
    elif db_points:
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
        # BS 7671 Section 701: NEVER place an inferred DB1 inside a wet room or outdoors!
        dry_indoor = [r for r in rooms if not r.is_wet_room and not r.is_outdoor]
        pref_order = (
            RoomType.PLANT_ROOM,
            RoomType.CORRIDOR,
            RoomType.UTILITY,
            RoomType.STORAGE,
            RoomType.STAIRWELL,
            RoomType.OPEN_OFFICE,
            RoomType.LIVING_ROOM,
            RoomType.KITCHEN_LIVING,
        )
        host_room: Room | None = None
        for rtype in pref_order:
            host_room = next((r for r in dry_indoor if r.normalized_type == rtype), None)
            if host_room is not None:
                break
        if host_room is None:
            host_room = dry_indoor[0] if dry_indoor else rooms[0]
        c_pt = host_room.centroid_mm()
        db_anchor = DistributionBoardAnchor(id="DB1", location_mm=c_pt)
        flags.append(
            ReviewFlag(
                code="DB_ANCHOR_INFERRED",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    f"No DB anchor found on layer A-DB; placed DB1 in dry indoor space "
                    f"'{host_room.raw_label}' ({c_pt.x_mm:.0f}, {c_pt.y_mm:.0f} mm). "
                    "Confirm DB location on plan."
                ),
            )
        )

    inferred_type = _infer_building_type(rooms)
    if building_type is not None:
        resolved_type = building_type
    elif did_auto_split_floors and inferred_type not in (
        BuildingType.OFFICE,
        BuildingType.SHOP,
        BuildingType.WORKSHOP,
    ):
        resolved_type = BuildingType.INDEPENDENT_HOUSE
    else:
        resolved_type = inferred_type
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
        sub_boards=sub_boards,
        pv_capacity_kva=pv_capacity_kva,
        source_file=str(dxf_path),
        source_sha256=sha256,
        source_unit_scale_to_mm=scale,
    )
    return model, flags


def _shift_point(pt: Point2D, dx_mm: float) -> Point2D:
    return Point2D(x_mm=round(pt.x_mm + dx_mm, 2), y_mm=round(pt.y_mm, 2))


def ingest_multi_floor_dxf(
    floor_files: list[tuple[str, Path]],
    *,
    submain_lengths_m: dict[str, float] | None = None,
    standards: StandardsBundle | None = None,
    project_name: str | None = None,
    building_type: BuildingType | None = None,
    pv_capacity_kva: float = 0.0,
    tier2_fallback: RoomClassifierFallback | None = None,
    layer_mapping: DxfLayerMapping | None = None,
    floor_gap_mm: float = 3000.0,
) -> tuple[BuildingModel, list[ReviewFlag]]:
    """Combine multiple architectural floor DXF files into one multi-board BuildingModel."""
    if not floor_files:
        raise ValueError("Provide at least one (floor_label, dxf_path) entry.")

    std = standards or load_standards()
    combined_rooms: list[Room] = []
    combined_walls: list[WallSegment] = []
    combined_doors: list[DoorOpening] = []
    combined_windows: list[WindowOpening] = []
    sub_boards: list[SubBoardSpec] = []
    floor_underlays: list[FloorUnderlayRef] = []
    combined_flags: list[ReviewFlag] = []
    hasher = hashlib.sha256()

    cursor_x = 0.0
    mdb_location: Point2D | None = None

    for idx, (raw_floor, dxf_path) in enumerate(floor_files):
        floor = raw_floor.strip().upper() or f"F{idx}"
        f_model, f_flags = ingest_dxf(
            dxf_path,
            standards=std,
            project_name=project_name,
            building_type=building_type,
            tier2_fallback=tier2_fallback,
            layer_mapping=layer_mapping,
            auto_split_multi_plan=False,
        )
        hasher.update(f"{floor}:{f_model.source_sha256 or ''}".encode())
        combined_flags.extend(f_flags)

        all_x = [p.x_mm for r in f_model.rooms for p in r.polygon_mm]
        min_x = min(all_x) if all_x else 0.0
        max_x = max(all_x) if all_x else 10_000.0
        dx = cursor_x - min_x
        floor_underlays.append(
            FloorUnderlayRef(
                floor=floor,
                source_file=str(dxf_path),
                dx_mm=round(dx, 2),
                unit_scale_to_mm=f_model.source_unit_scale_to_mm,
            )
        )

        for r in f_model.rooms:
            new_rid = f"{floor}-{r.id}"
            shifted_fixts = [
                PlumbingFixture(
                    id=f"{floor}-{fx.id}",
                    room_id=new_rid,
                    fixture_type=fx.fixture_type,
                    polygon_mm=[_shift_point(p, dx) for p in fx.polygon_mm],
                )
                for fx in r.fixtures
            ]
            shifted_furn = [
                FurnitureItem(
                    id=f"{floor}-{fn.id}",
                    room_id=new_rid,
                    furniture_type=fn.furniture_type,
                    polygon_mm=[_shift_point(p, dx) for p in fn.polygon_mm],
                    centroid_mm=_shift_point(fn.centroid_mm, dx),
                )
                for fn in r.furniture
            ]
            combined_rooms.append(
                r.model_copy(
                    update={
                        "id": new_rid,
                        "floor": floor,
                        "raw_label": f"{r.raw_label} ({floor})",
                        "polygon_mm": [_shift_point(p, dx) for p in r.polygon_mm],
                        "fixtures": shifted_fixts,
                        "furniture": shifted_furn,
                    }
                )
            )

        for w in f_model.walls:
            combined_walls.append(
                w.model_copy(
                    update={
                        "id": f"{floor}-{w.id}",
                        "start": _shift_point(w.start, dx),
                        "end": _shift_point(w.end, dx),
                    }
                )
            )
        for d in f_model.doors:
            combined_doors.append(
                d.model_copy(
                    update={
                        "id": f"{floor}-{d.id}",
                        "room_id": f"{floor}-{d.room_id}",
                        "hinge": _shift_point(d.hinge, dx),
                    }
                )
            )
        for win in f_model.windows:
            combined_windows.append(
                win.model_copy(
                    update={
                        "id": f"{floor}-{win.id}",
                        "room_id": f"{floor}-{win.room_id}",
                        "start": _shift_point(win.start, dx),
                        "end": _shift_point(win.end, dx),
                    }
                )
            )

        shifted_db = _shift_point(f_model.db_anchor.location_mm, dx)
        if mdb_location is None:
            mdb_location = Point2D(x_mm=max(0.0, shifted_db.x_mm - 500.0), y_mm=shifted_db.y_mm)

        default_len = 6.0 + 10.0 * idx
        sm_len = (
            submain_lengths_m.get(floor, default_len)
            if submain_lengths_m is not None
            else default_len
        )
        sub_boards.append(
            SubBoardSpec(
                board_id=f"DB-{floor}",
                floor=floor,
                parent_board_id="MDB",
                location_mm=shifted_db,
                submain_length_m=sm_len,
                is_three_phase=True,
            )
        )
        cursor_x = (max_x + dx) + floor_gap_mm

    inferred_mf = _infer_building_type(combined_rooms)
    resolved_type = building_type or (
        inferred_mf
        if inferred_mf in (BuildingType.OFFICE, BuildingType.SHOP, BuildingType.WORKSHOP)
        else BuildingType.INDEPENDENT_HOUSE
    )
    total_area = round(sum(r.area_m2 for r in combined_rooms), 4)
    floor_list = ", ".join(sb.floor for sb in sub_boards)

    combined_flags.append(
        ReviewFlag(
            code="MULTIFLOOR_DXF_COMBINED",
            severity=ReviewSeverity.INFO,
            category="geometry",
            message=(
                f"Combined {len(sub_boards)} architectural floor DXF file(s) ({floor_list}) "
                f"into unified MDB -> {', '.join(sb.board_id for sb in sub_boards)} hierarchy "
                f"({total_area:.1f} m² total)."
            ),
        )
    )

    assert mdb_location is not None
    model = BuildingModel(
        project_name=project_name or f"Multi-Floor Building ({floor_list})",
        building_type=resolved_type,
        units="mm",
        total_floor_area_m2=total_area,
        rooms=combined_rooms,
        walls=combined_walls,
        doors=combined_doors,
        windows=combined_windows,
        db_anchor=DistributionBoardAnchor(id="MDB", location_mm=mdb_location),
        sub_boards=sub_boards if len(sub_boards) >= 2 else [],
        pv_capacity_kva=pv_capacity_kva,
        source_file=",".join(str(p) for _, p in floor_files),
        source_sha256=hasher.hexdigest(),
        floor_underlays=floor_underlays,
    )
    return model, combined_flags
