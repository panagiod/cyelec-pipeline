"""Deterministic BS 7671 Section 701 wet-zone polygon generator clipped to room boundaries."""

from __future__ import annotations

from typing import Literal

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from cyelec.models.building import BuildingModel, Point2D, Room
from cyelec.models.electrical import EacProfile
from cyelec.models.spatial import RoomWetZones
from cyelec.standards_loader import StandardsBundle

ZONE_2_RADIUS_MM = 600.0


def _geom_to_point_rings(geom: BaseGeometry) -> list[list[Point2D]]:
    """Convert a Shapely Polygon / MultiPolygon into simplified outer Point2D rings."""
    if geom.is_empty:
        return []
    polys: list[Polygon] = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = [p for p in geom.geoms if not p.is_empty]
    else:
        return []

    rings: list[list[Point2D]] = []
    for poly in polys:
        if poly.area < 1.0:
            continue
        simplified = poly.simplify(5.0, preserve_topology=True)
        if not isinstance(simplified, Polygon) or simplified.is_empty:
            continue
        coords = list(simplified.exterior.coords)[:-1]
        if len(coords) >= 3:
            rings.append(
                [Point2D(x_mm=round(float(x), 1), y_mm=round(float(y), 1)) for x, y in coords]
            )
    return rings


def _room_polygon(room: Room) -> Polygon:
    return Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])


def compute_room_wet_zones(
    room: Room,
    *,
    exclusion_radius_m: float,
) -> RoomWetZones | None:
    """Compute BS 7671 Section 701 Zone 0/1/2 and EAC socket exclusion polygons for one room."""
    wet_fixtures = [
        f for f in room.fixtures if f.fixture_type in ("bathtub", "shower_tray")
    ]
    if not wet_fixtures:
        return None

    room_poly = _room_polygon(room)
    fixture_polys = [
        Polygon([(p.x_mm, p.y_mm) for p in f.polygon_mm]).intersection(room_poly)
        for f in wet_fixtures
    ]
    zone0_geom = unary_union(fixture_polys)
    # In 2D plan view, Zone 1 directly overlays bathtub / shower tray footprint
    zone1_geom = zone0_geom
    # Zone 2 extends 600 mm horizontally from Zone 1, clipped strictly to room walls
    zone2_geom = (
        zone1_geom.buffer(ZONE_2_RADIUS_MM, quad_segs=16)
        .intersection(room_poly)
        .difference(zone1_geom)
    )
    # EAC / BS 7671 Reg 701.512.3 socket exclusion: 2.5 m (18th A2) or 3.0 m (17th A3) from Zone 1
    excl_mm = exclusion_radius_m * 1000.0
    excl_geom = zone1_geom.buffer(excl_mm, quad_segs=16).intersection(room_poly)

    return RoomWetZones(
        room_id=room.id,
        fixture_ids=[f.id for f in wet_fixtures],
        exclusion_radius_m=exclusion_radius_m,
        zone_0_polygons_mm=_geom_to_point_rings(zone0_geom),
        zone_1_polygons_mm=_geom_to_point_rings(zone1_geom),
        zone_2_polygons_mm=_geom_to_point_rings(zone2_geom),
        socket_exclusion_polygons_mm=_geom_to_point_rings(excl_geom),
    )


def compute_building_wet_zones(
    building: BuildingModel,
    standards: StandardsBundle,
    *,
    eac_profile: EacProfile | None = None,
) -> list[RoomWetZones]:
    """Compute BS 7671 Section 701 wet zones for all wet rooms in a BuildingModel."""
    active_profile = eac_profile or standards.eac_rules.default_profile
    excl_m = standards.eac_rules.profiles[active_profile].bathroom_socket_exclusion_m

    results: list[RoomWetZones] = []
    for room in building.rooms:
        wz = compute_room_wet_zones(room, exclusion_radius_m=excl_m)
        if wz is not None:
            results.append(wz)
    return results


def _rings_to_union(rings: list[list[Point2D]]) -> BaseGeometry:
    if not rings:
        return Polygon()
    return unary_union([Polygon([(p.x_mm, p.y_mm) for p in ring]) for ring in rings])


def classify_point_wet_zone(
    point: Point2D,
    wet_zone: RoomWetZones | None,
) -> Literal["zone_0", "zone_1", "zone_2", "socket_exclusion", "outside"]:
    """Classify a 2D point relative to a room's BS 7671 Section 701 wet zones."""
    if wet_zone is None:
        return "outside"
    pt = Point(point.x_mm, point.y_mm)
    z0 = _rings_to_union(wet_zone.zone_0_polygons_mm)
    if not z0.is_empty and (z0.contains(pt) or z0.touches(pt)):
        return "zone_0"
    z2 = _rings_to_union(wet_zone.zone_2_polygons_mm)
    if not z2.is_empty and (z2.contains(pt) or z2.touches(pt)):
        return "zone_2"
    excl = _rings_to_union(wet_zone.socket_exclusion_polygons_mm)
    if not excl.is_empty and (excl.contains(pt) or excl.touches(pt)):
        return "socket_exclusion"
    return "outside"


def is_point_in_socket_exclusion(point: Point2D, wet_zone: RoomWetZones | None) -> bool:
    """Return True if a point falls inside Zone 0/1/2 or the EAC socket exclusion region."""
    return classify_point_wet_zone(point, wet_zone) != "outside"
