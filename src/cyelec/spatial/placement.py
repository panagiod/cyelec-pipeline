"""Deterministic valid-wall electrical placement avoiding doors, swings, windows, wet zones."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from cyelec.models.building import BuildingModel, DoorOpening, Point2D, Room, WindowOpening
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.models.spatial import DeviceType, PlacedDevice, RoomWetZones
from cyelec.spatial.wet_zones import classify_point_wet_zone
from cyelec.standards_loader import StandardsBundle

CORNER_CLEARANCE_MM = 220.0
DOOR_CLEARANCE_MM = 220.0
WINDOW_CLEARANCE_MM = 150.0
MIN_SOCKET_SPACING_MM = 600.0


@dataclass(frozen=True)
class _WallSegmentCandidate:
    line: LineString
    angle_deg: float
    is_exterior: bool


def _door_forbidden_geometry(door: DoorOpening) -> BaseGeometry:
    """Build conservative 2D sweep polygon covering the door opening and 90-degree leaf arc."""
    hx, hy = door.hinge.x_mm, door.hinge.y_mm
    r = door.width_mm
    start = door.angle_deg
    sweep = 90.0 if door.swing_dir == "ccw" else -90.0
    arc_pts: list[tuple[float, float]] = [(hx, hy)]
    for step in range(13):
        ang = math.radians(start + sweep * (step / 12.0))
        arc_pts.append((hx + r * math.cos(ang), hy + r * math.sin(ang)))
    arc_pts.append((hx, hy))
    sector = Polygon(arc_pts)
    # Also cover frame clearance at hinge and latch jamb along the wall
    jamb_rad = math.radians(start)
    latch_pt = (hx + r * math.cos(jamb_rad), hy + r * math.sin(jamb_rad))
    frame_line = LineString([(hx, hy), latch_pt]).buffer(DOOR_CLEARANCE_MM)
    return unary_union([sector.buffer(60.0), frame_line])


def _window_forbidden_geometry(win: WindowOpening) -> BaseGeometry:
    return LineString(
        [(win.start.x_mm, win.start.y_mm), (win.end.x_mm, win.end.y_mm)]
    ).buffer(WINDOW_CLEARANCE_MM)


def _trim_ends(line: LineString, trim_mm: float) -> LineString | None:
    if line.length <= 2.0 * trim_mm + 50.0:
        return None
    p0 = line.interpolate(trim_mm)
    p1 = line.interpolate(line.length - trim_mm)
    return LineString([(p0.x, p0.y), (p1.x, p1.y)])


def _extract_linestrings(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom] if geom.length > 50.0 else []
    if isinstance(geom, MultiLineString):
        return [ls for ls in geom.geoms if ls.length > 50.0]
    return []


def compute_valid_wall_segments(
    room: Room,
    *,
    doors: list[DoorOpening],
    windows: list[WindowOpening],
    wet_zone: RoomWetZones | None,
    building_outer_ring: BaseGeometry,
    exclude_wet_zones: bool = True,
) -> list[_WallSegmentCandidate]:
    """Compute valid interior wall segments for placing wall-mounted accessories in a room."""
    pts = room.polygon_mm
    forbidden_parts: list[BaseGeometry] = [
        _door_forbidden_geometry(d) for d in doors
    ]
    forbidden_parts.extend(_window_forbidden_geometry(w) for w in windows)
    if exclude_wet_zones and wet_zone is not None:
        for ring in wet_zone.socket_exclusion_polygons_mm:
            forbidden_parts.append(Polygon([(p.x_mm, p.y_mm) for p in ring]).buffer(20.0))

    forbidden = unary_union(forbidden_parts) if forbidden_parts else Polygon()

    candidates: list[_WallSegmentCandidate] = []
    n = len(pts)
    for i in range(n):
        a = pts[i]
        b = pts[(i + 1) % n]
        edge = LineString([(a.x_mm, a.y_mm), (b.x_mm, b.y_mm)])
        trimmed = _trim_ends(edge, CORNER_CLEARANCE_MM)
        if trimmed is None:
            continue
        angle_deg = round(math.degrees(math.atan2(b.y_mm - a.y_mm, b.x_mm - a.x_mm)) % 360.0, 1)
        mid = trimmed.interpolate(0.5, normalized=True)
        is_ext = float(building_outer_ring.distance(mid)) < 60.0

        remaining = trimmed.difference(forbidden) if not forbidden.is_empty else trimmed
        for seg in _extract_linestrings(remaining):
            if seg.length >= 250.0:
                candidates.append(
                    _WallSegmentCandidate(line=seg, angle_deg=angle_deg, is_exterior=is_ext)
                )

    return candidates


def _place_points_along_candidates(
    candidates: list[_WallSegmentCandidate],
    count: int,
    *,
    min_spacing_mm: float = MIN_SOCKET_SPACING_MM,
) -> list[tuple[Point2D, float]]:
    """Deterministically distribute up to `count` points evenly across valid wall segments."""
    if count <= 0 or not candidates:
        return []

    total_len = sum(c.line.length for c in candidates)
    if total_len < 200.0:
        return []

    # Allocate slots proportionally by segment length while enforcing min spacing
    ordered = sorted(candidates, key=lambda c: c.line.length, reverse=True)
    max_per_seg = [max(1, int(c.line.length // min_spacing_mm)) for c in ordered]
    alloc = [0] * len(ordered)
    remaining = min(count, sum(max_per_seg))

    # Round-robin proportional allocation favoring longer walls first
    while remaining > 0:
        progressed = False
        for i in range(len(ordered)):
            if remaining <= 0:
                break
            if alloc[i] < max_per_seg[i]:
                alloc[i] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break

    placed: list[tuple[Point2D, float]] = []
    for seg_cand, k in zip(ordered, alloc, strict=False):
        if k <= 0:
            continue
        for j in range(k):
            frac = (j + 1) / (k + 1)
            pt = seg_cand.line.interpolate(frac, normalized=True)
            placed.append(
                (
                    Point2D(x_mm=round(float(pt.x), 1), y_mm=round(float(pt.y), 1)),
                    seg_cand.angle_deg,
                )
            )
    return placed


def _place_ceiling_luminaires(room: Room, count: int) -> list[Point2D]:
    """Place ceiling luminaires on a balanced interior grid strictly inside the room polygon."""
    poly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
    if count <= 1:
        c = poly.representative_point()
        return [Point2D(x_mm=round(float(c.x), 1), y_mm=round(float(c.y), 1))]

    minx, miny, maxx, maxy = poly.bounds
    w = maxx - minx
    h = maxy - miny
    cols = count if w >= h else 1
    rows = 1 if w >= h else count
    if count >= 4:
        cols = 2
        rows = math.ceil(count / 2)

    pts: list[Point2D] = []
    for r in range(rows):
        for c in range(cols):
            if len(pts) >= count:
                break
            px = minx + w * ((c + 1) / (cols + 1))
            py = miny + h * ((r + 1) / (rows + 1))
            if poly.contains(Point(px, py)):
                pts.append(Point2D(x_mm=round(px, 1), y_mm=round(py, 1)))
    if not pts:
        rp = poly.representative_point()
        pts.append(Point2D(x_mm=round(float(rp.x), 1), y_mm=round(float(rp.y), 1)))
    return pts


def _place_room_switch(
    room: Room,
    room_doors: list[DoorOpening],
    valid_walls: list[_WallSegmentCandidate],
    wet_zone: RoomWetZones | None,
) -> tuple[Point2D, float]:
    """Place light switch on latch side of entry door (outside wet rooms per Section 701)."""
    poly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
    boundary = poly.exterior

    if room_doors:
        door = room_doors[0]
        hx, hy = door.hinge.x_mm, door.hinge.y_mm
        rad = math.radians(door.angle_deg)
        # Latch side sits 180 mm past door width along the wall line
        dist = door.width_mm + 180.0
        sx = hx + dist * math.cos(rad)
        sy = hy + dist * math.sin(rad)

        # For wet rooms, offset 120 mm outward across the door wall (corridor side)
        if room.is_wet_room:
            nx, ny = -math.sin(rad), math.cos(rad)
            cand_a = Point(sx + 120.0 * nx, sy + 120.0 * ny)
            cand_b = Point(sx - 120.0 * nx, sy - 120.0 * ny)
            chosen = cand_a if not poly.contains(cand_a) else cand_b
            return (
                Point2D(x_mm=round(float(chosen.x), 1), y_mm=round(float(chosen.y), 1)),
                round(door.angle_deg % 360.0, 1),
            )

        # Guard against corner overshoot: latch-side switch must remain on the room boundary
        cand_shapely = Point(sx, sy)
        cand_pt = Point2D(x_mm=round(sx, 1), y_mm=round(sy, 1))
        if (
            float(boundary.distance(cand_shapely)) <= 120.0
            and classify_point_wet_zone(cand_pt, wet_zone) == "outside"
        ):
            return cand_pt, round(door.angle_deg % 360.0, 1)

    if valid_walls:
        pt = valid_walls[0].line.interpolate(0.15, normalized=True)
        return (
            Point2D(x_mm=round(float(pt.x), 1), y_mm=round(float(pt.y), 1)),
            valid_walls[0].angle_deg,
        )

    c = room.centroid_mm()
    return c, 0.0


def place_electrical_devices(
    building: BuildingModel,
    wet_zones: list[RoomWetZones],
    standards: StandardsBundle,
) -> tuple[list[PlacedDevice], list[ReviewFlag]]:
    """Deterministically place luminaires, switches, sockets, A/C units, and dedicated outlets."""
    tmpl_std = standards.room_templates
    wz_by_room = {w.room_id: w for w in wet_zones}

    # Exact building outer boundary via unary_union (accurate on L- and U-shaped floorplans)
    all_room_polys = [Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm]) for r in building.rooms]
    outer_ring: BaseGeometry = unary_union(all_room_polys).boundary

    devices: list[PlacedDevice] = []
    flags: list[ReviewFlag] = []
    counters: dict[str, int] = {
        "L": 0,
        "SW": 0,
        "S": 0,
        "AC": 0,
        "APP": 0,
    }

    def _next_dev(prefix: str) -> str:
        counters[prefix] += 1
        return f"DEV-{prefix}{counters[prefix]}"

    emitted_appliance_keys: set[str] = set()

    for room in building.rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        room_wz = wz_by_room.get(room.id)
        rpoly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
        # Match both doors owned by this room AND outward-swinging doors touching its perimeter
        room_doors = [
            d
            for d in building.doors
            if d.room_id == room.id
            or float(rpoly.exterior.distance(Point(d.hinge.x_mm, d.hinge.y_mm))) <= 120.0
        ]
        room_windows = [w for w in building.windows if w.room_id == room.id]

        # Valid walls strictly excluding doors, swings, windows, and Section 701 exclusion zones
        valid_walls = compute_valid_wall_segments(
            room,
            doors=building.doors,  # check all doors touching this room's boundary
            windows=room_windows,
            wet_zone=room_wz,
            building_outer_ring=outer_ring,
            exclude_wet_zones=True,
        )

        # 1. Ceiling Luminaires
        lum_pts = _place_ceiling_luminaires(room, max(1, rt.min_lighting_points))
        for idx, lpt in enumerate(lum_pts, start=1):
            zone = classify_point_wet_zone(lpt, room_wz)
            if room.is_outdoor:
                ip = "IP65"
            elif zone in ("zone_0", "zone_1", "zone_2") or room.is_wet_room:
                ip = "IP44"
            else:
                ip = "IP20"

            devices.append(
                PlacedDevice(
                    device_id=_next_dev("L"),
                    room_id=room.id,
                    device_type=DeviceType.LUMINAIRE,
                    label=f"Luminaire {idx} ({room.raw_label})",
                    location_mm=lpt,
                    mounting_height_mm=room.ceiling_height_mm,
                    ip_rating=ip,
                )
            )

        # 2. Room Light Switch
        sw_pt, sw_ang = _place_room_switch(room, room_doors, valid_walls, room_wz)
        devices.append(
            PlacedDevice(
                device_id=_next_dev("SW"),
                room_id=room.id,
                device_type=DeviceType.SWITCH,
                label=f"Light Switch ({room.raw_label})",
                location_mm=sw_pt,
                mounting_height_mm=1200.0,
                wall_angle_deg=sw_ang,
                ip_rating="IP65" if room.is_outdoor else "IP20",
            )
        )

        # 3. Twin 13A Socket Outlets (never placed in wet-zone socket exclusion!)
        if rt.socket_count_default > 0 and rt.socket_group != "none":
            sock_pts = _place_points_along_candidates(valid_walls, rt.socket_count_default)
            mount_h = 1150.0 if room.normalized_type.value in ("kitchen", "utility") else 450.0
            sock_ip = "IP65" if room.is_outdoor else "IP20"
            for idx, (spt, sang) in enumerate(sock_pts, start=1):
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("S"),
                        room_id=room.id,
                        device_type=DeviceType.SOCKET_TWIN,
                        label=f"Twin 13A Socket {idx} ({room.raw_label})",
                        location_mm=spt,
                        mounting_height_mm=mount_h,
                        wall_angle_deg=sang,
                        ip_rating=sock_ip,
                    )
                )
            if len(sock_pts) < rt.socket_count_default:
                flags.append(
                    ReviewFlag(
                        code="INSUFFICIENT_VALID_WALL_FOR_SOCKETS",
                        severity=ReviewSeverity.WARNING,
                        category="geometry",
                        message=(
                            f"Room {room.id} ({room.raw_label}) placed {len(sock_pts)}/"
                            f"{rt.socket_count_default} default sockets due to door/window/"
                            "wet-zone wall clearances."
                        ),
                    )
                )

        # 4. Split A/C Unit & Isolator (prefer exterior valid wall for short refrigerant run)
        if rt.has_split_ac and rt.ac_electrical_w > 0.0:
            ext_walls = [c for c in valid_walls if c.is_exterior]
            ac_pool = ext_walls if ext_walls else valid_walls
            if ac_pool:
                best_wall = max(ac_pool, key=lambda c: c.line.length)
                pt = best_wall.line.interpolate(0.5, normalized=True)
                ac_loc = Point2D(x_mm=round(float(pt.x), 1), y_mm=round(float(pt.y), 1))
                ac_ang = best_wall.angle_deg
            else:
                ac_loc = room.centroid_mm()
                ac_ang = 0.0
            devices.append(
                PlacedDevice(
                    device_id=_next_dev("AC"),
                    room_id=room.id,
                    device_type=DeviceType.SPLIT_AC,
                    label=f"Split A/C Isolator ({room.raw_label})",
                    location_mm=ac_loc,
                    mounting_height_mm=2200.0,
                    wall_angle_deg=ac_ang,
                    ip_rating="IP20",
                )
            )

        # 5. Dedicated Appliance Outlets (Cooker, Oven, Dishwasher, Washer, Solar Water Heater)
        room_apps = [a for a in rt.dedicated_appliances if a.key not in emitted_appliance_keys]
        if room_apps:
            app_pts = _place_points_along_candidates(
                valid_walls, len(room_apps), min_spacing_mm=450.0
            )
            for idx, app in enumerate(room_apps):
                emitted_appliance_keys.add(app.key)
                if idx < len(app_pts):
                    loc, ang = app_pts[idx]
                else:
                    loc, ang = room.centroid_mm(), 0.0

                if app.category.value == "cooker":
                    dtype = DeviceType.COOKER_OUTLET
                elif app.category.value == "oven":
                    dtype = DeviceType.OVEN_OUTLET
                elif app.category.value == "water_heater":
                    dtype = DeviceType.WATER_HEATER_ISOLATOR
                else:
                    dtype = DeviceType.APPLIANCE_OUTLET

                app_height = 1200.0 if app.category.value == "water_heater" else 450.0
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("APP"),
                        room_id=room.id,
                        device_type=dtype,
                        label=f"{app.label} ({room.raw_label})",
                        location_mm=loc,
                        mounting_height_mm=app_height,
                        wall_angle_deg=ang,
                        ip_rating="IP20",
                        appliance_key=app.key,
                    )
                )

    return devices, flags
