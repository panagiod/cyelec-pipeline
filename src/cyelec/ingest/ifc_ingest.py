"""Optional IFC2x3/IFC4 BIM ingestion module backed by LGPLv3 ifcopenshell."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from shapely.geometry import Point, Polygon

from cyelec.ingest.room_classifier import RoomClassifierFallback, classify_room_label
from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    PlumbingFixture,
    Point2D,
    Room,
    RoomType,
)
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.standards_loader import StandardsBundle, load_standards

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IfcSpaceRecord:
    """Normalized 2D footprint record extracted from an IfcSpace entity."""

    global_id: str
    name: str
    polygon_mm: list[Point2D]
    ceiling_height_mm: float = 2900.0


@dataclass(frozen=True)
class IfcFixtureRecord:
    """Normalized 2D footprint record extracted from an IfcSanitaryTerminal entity."""

    global_id: str
    predefined_type: str
    polygon_mm: list[Point2D]


def _map_ifc_sanitary_type(
    predefined_type: str,
) -> Literal["bathtub", "shower_tray", "basin", "wc", "sink"]:
    up = predefined_type.upper()
    if "BATH" in up:
        return "bathtub"
    if "SHOWER" in up:
        return "shower_tray"
    if "BASIN" in up or "LAVATORY" in up:
        return "basin"
    if "SINK" in up:
        return "sink"
    return "wc"


def build_model_from_ifc_records(
    *,
    spaces: list[IfcSpaceRecord],
    fixtures: list[IfcFixtureRecord] | None = None,
    db_location_mm: Point2D | None = None,
    project_name: str = "IFC Architectural Model",
    building_type: BuildingType | None = None,
    pv_capacity_kva: float = 0.0,
    source_file: str | None = None,
    source_sha256: str | None = None,
    standards: StandardsBundle | None = None,
    tier2_fallback: RoomClassifierFallback | None = None,
) -> tuple[BuildingModel, list[ReviewFlag]]:
    """Convert extracted IFC space & sanitary records into a validated BuildingModel."""
    if not spaces:
        raise ValueError("IFC model contains no IfcSpace records.")

    std = standards or load_standards()
    tmpl_std = std.room_templates
    flags: list[ReviewFlag] = []

    rooms: list[Room] = []
    room_polys: list[tuple[str, Polygon]] = []

    for idx, sp in enumerate(spaces, start=1):
        rid = f"R{idx}"
        poly = Polygon([(p.x_mm, p.y_mm) for p in sp.polygon_mm])
        if not poly.is_valid or poly.area <= 0.0:
            poly = poly.buffer(0.0)
        area_m2 = round(float(poly.area) / 1_000_000.0, 4)

        cls_res = classify_room_label(
            sp.name,
            tmpl_std,
            room_id=rid,
            tier2_fallback=tier2_fallback,
        )
        if cls_res.flag is not None:
            flags.append(cls_res.flag)

        rt = tmpl_std.rooms[cls_res.normalized_type]
        room = Room(
            id=rid,
            raw_label=cls_res.cleaned_label or sp.name,
            normalized_type=cls_res.normalized_type,
            polygon_mm=sp.polygon_mm,
            area_m2=area_m2,
            ceiling_height_mm=sp.ceiling_height_mm,
            is_wet_room=rt.is_wet_room,
            is_outdoor=rt.is_outdoor,
            ambient_temp_c=(
                std.eac_rules.ambient_defaults_c.roof_outdoor_c if rt.is_outdoor else None
            ),
            fixtures=[],
        )
        rooms.append(room)
        room_polys.append((rid, poly))

    rooms_by_id = {r.id: r for r in rooms}

    for f_idx, fixt_rec in enumerate(fixtures or [], start=1):
        fpoly = Polygon([(p.x_mm, p.y_mm) for p in fixt_rec.polygon_mm])
        c: Point = fpoly.centroid
        target_rid: str | None = None
        for rid, rpoly in room_polys:
            if rpoly.contains(c) or rpoly.touches(c):
                target_rid = rid
                break
        if target_rid is None:
            continue
        ftype = _map_ifc_sanitary_type(fixt_rec.predefined_type)
        room_obj = rooms_by_id[target_rid]
        room_obj.fixtures.append(
            PlumbingFixture(
                id=f"IFC-FIXT-{f_idx}",
                room_id=target_rid,
                fixture_type=ftype,
                polygon_mm=fixt_rec.polygon_mm,
            )
        )
        if ftype in ("bathtub", "shower_tray"):
            room_obj.is_wet_room = True

    if db_location_mm is not None:
        db_anchor = DistributionBoardAnchor(id="DB1", location_mm=db_location_mm)
    else:
        corridor = next((r for r in rooms if r.normalized_type == RoomType.CORRIDOR), rooms[0])
        db_anchor = DistributionBoardAnchor(id="DB1", location_mm=corridor.centroid_mm())
        flags.append(
            ReviewFlag(
                code="DB_ANCHOR_INFERRED",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    "No IfcElectricDistributionBoard found in IFC model; placed DB1 at "
                    f"{corridor.raw_label} centroid."
                ),
            )
        )

    bed_count = sum(
        1 for r in rooms if r.normalized_type in (RoomType.MASTER_BEDROOM, RoomType.BEDROOM)
    )
    inferred_type = (
        BuildingType.APARTMENT_3BED_PLUS
        if bed_count >= 3
        else (
            BuildingType.APARTMENT_2BED
            if bed_count == 2
            else (BuildingType.APARTMENT_1BED if bed_count == 1 else BuildingType.STUDIO)
        )
    )

    model = BuildingModel(
        project_name=project_name,
        building_type=building_type or inferred_type,
        units="mm",
        total_floor_area_m2=round(sum(r.area_m2 for r in rooms), 4),
        rooms=rooms,
        db_anchor=db_anchor,
        pv_capacity_kva=pv_capacity_kva,
        source_file=source_file,
        source_sha256=source_sha256,
    )
    return model, flags


def ingest_ifc(
    ifc_path: Path,
    *,
    standards: StandardsBundle | None = None,
    standards_dir: Path | None = None,
    project_name: str | None = None,
    building_type: BuildingType | None = None,
    pv_capacity_kva: float = 0.0,
    tier2_fallback: RoomClassifierFallback | None = None,
) -> tuple[BuildingModel, list[ReviewFlag]]:
    """Ingest an IFC2x3/IFC4 file using optional LGPLv3 ifcopenshell."""
    try:
        import ifcopenshell
        import ifcopenshell.geom
        import ifcopenshell.util.unit
    except ImportError as exc:
        raise ImportError(
            "IFC ingestion requires the optional 'bim' dependency (LGPLv3 ifcopenshell). "
            "Install with: pip install 'cyelec-pipeline[bim]'"
        ) from exc

    if not ifc_path.is_file():
        raise FileNotFoundError(f"IFC file not found: {ifc_path}")

    std = standards or load_standards(standards_dir)
    sha256 = hashlib.sha256(ifc_path.read_bytes()).hexdigest()
    ifc_file = ifcopenshell.open(str(ifc_path))

    unit_scale_m = float(ifcopenshell.util.unit.calculate_unit_scale(ifc_file))
    to_mm = unit_scale_m * 1000.0
    settings = ifcopenshell.geom.settings()

    spaces: list[IfcSpaceRecord] = []
    for sp in ifc_file.by_type("IfcSpace"):
        label = str(sp.LongName or sp.Name or sp.GlobalId)
        try:
            shape = ifcopenshell.geom.create_shape(settings, sp)
            verts = shape.geometry.verts
            xs = [verts[i] * 1000.0 for i in range(0, len(verts), 3)]
            ys = [verts[i + 1] * 1000.0 for i in range(0, len(verts), 3)]
            zs = [verts[i + 2] * 1000.0 for i in range(0, len(verts), 3)]
            if not xs:
                continue
            minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
            height = max(2400.0, (max(zs) - min(zs)) if zs else 2900.0)
            spaces.append(
                IfcSpaceRecord(
                    global_id=str(sp.GlobalId),
                    name=label,
                    polygon_mm=[
                        Point2D(x_mm=round(minx, 2), y_mm=round(miny, 2)),
                        Point2D(x_mm=round(maxx, 2), y_mm=round(miny, 2)),
                        Point2D(x_mm=round(maxx, 2), y_mm=round(maxy, 2)),
                        Point2D(x_mm=round(minx, 2), y_mm=round(maxy, 2)),
                    ],
                    ceiling_height_mm=round(height, 1),
                )
            )
        except Exception:
            _logger.debug("Skipping IFC space with unreadable geometry.", exc_info=True)
            continue

    fixtures: list[IfcFixtureRecord] = []
    for term in ifc_file.by_type("IfcSanitaryTerminal"):
        try:
            shape = ifcopenshell.geom.create_shape(settings, term)
            verts = shape.geometry.verts
            xs = [verts[i] * 1000.0 for i in range(0, len(verts), 3)]
            ys = [verts[i + 1] * 1000.0 for i in range(0, len(verts), 3)]
            if not xs:
                continue
            minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
            fixtures.append(
                IfcFixtureRecord(
                    global_id=str(term.GlobalId),
                    predefined_type=str(getattr(term, "PredefinedType", "USERDEFINED") or ""),
                    polygon_mm=[
                        Point2D(x_mm=round(minx, 2), y_mm=round(miny, 2)),
                        Point2D(x_mm=round(maxx, 2), y_mm=round(miny, 2)),
                        Point2D(x_mm=round(maxx, 2), y_mm=round(maxy, 2)),
                        Point2D(x_mm=round(minx, 2), y_mm=round(maxy, 2)),
                    ],
                )
            )
        except Exception:
            _logger.debug("Skipping IFC sanitary terminal with unreadable geometry.", exc_info=True)
            continue

    db_loc: Point2D | None = None
    for board in ifc_file.by_type("IfcElectricDistributionBoard"):
        placement = getattr(board, "ObjectPlacement", None)
        rel = getattr(placement, "RelativePlacement", None)
        loc = getattr(rel, "Location", None)
        coords = getattr(loc, "Coordinates", None)
        if coords and len(coords) >= 2:
            db_loc = Point2D(
                x_mm=round(float(coords[0]) * to_mm, 2),
                y_mm=round(float(coords[1]) * to_mm, 2),
            )
            break

    return build_model_from_ifc_records(
        spaces=spaces,
        fixtures=fixtures,
        db_location_mm=db_loc,
        project_name=project_name or ifc_path.stem.replace("_", " ").title(),
        building_type=building_type,
        pv_capacity_kva=pv_capacity_kva,
        source_file=str(ifc_path),
        source_sha256=sha256,
        standards=std,
        tier2_fallback=tier2_fallback,
    )
