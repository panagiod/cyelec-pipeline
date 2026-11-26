"""Deterministic valid-wall electrical placement avoiding doors, swings, windows, wet zones."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from cyelec.models.building import (
    BuildingModel,
    DoorOpening,
    Point2D,
    Room,
    RoomType,
    WindowOpening,
)
from cyelec.models.electrical import ResidentialSystemsBrief, ReviewFlag, ReviewSeverity
from cyelec.models.spatial import DeviceType, PlacedDevice, RoomWetZones, SwitchLeg
from cyelec.solver.grouper import is_commercial_building
from cyelec.spatial.stage5_placement import place_stage5_residential_systems
from cyelec.spatial.wet_zones import classify_point_wet_zone
from cyelec.standards_loader import StandardsBundle

CORNER_CLEARANCE_MM = 220.0
DOOR_CLEARANCE_MM = 220.0
WINDOW_CLEARANCE_MM = 150.0
MIN_SOCKET_SPACING_MM = 600.0

# BS EN 12464 / CIBSE target illuminance (lux) & typical Cyprus LED fixture power (W)
_ROOM_LIGHTING_SPECS: dict[RoomType, tuple[int, float]] = {
    RoomType.LIVING_ROOM: (150, 18.0),
    RoomType.KITCHEN: (300, 18.0),
    RoomType.KITCHEN_LIVING: (200, 18.0),
    RoomType.MASTER_BEDROOM: (150, 15.0),
    RoomType.BEDROOM: (150, 15.0),
    RoomType.BATHROOM: (200, 12.0),
    RoomType.ENSUITE: (200, 12.0),
    RoomType.WC: (200, 12.0),
    RoomType.CORRIDOR: (100, 12.0),
    RoomType.UTILITY: (200, 15.0),
    RoomType.VERANDA: (100, 15.0),
    RoomType.STORAGE: (100, 12.0),
    RoomType.OPEN_OFFICE: (500, 36.0),
    RoomType.MEETING_ROOM: (500, 36.0),
    RoomType.SERVER_ROOM: (300, 24.0),
    RoomType.RETAIL_FLOOR: (300, 36.0),
    RoomType.WAREHOUSE: (200, 36.0),
    RoomType.PLANT_ROOM: (200, 24.0),
    RoomType.STAIRWELL: (150, 12.0),
    RoomType.LIFT_SHAFT: (100, 12.0),
}


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
    return LineString([(win.start.x_mm, win.start.y_mm), (win.end.x_mm, win.end.y_mm)]).buffer(
        WINDOW_CLEARANCE_MM
    )


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
    forbidden_parts: list[BaseGeometry] = [_door_forbidden_geometry(d) for d in doors]
    forbidden_parts.extend(_window_forbidden_geometry(w) for w in windows)
    if exclude_wet_zones and wet_zone is not None:
        for ring in wet_zone.socket_exclusion_polygons_mm:
            forbidden_parts.append(Polygon([(p.x_mm, p.y_mm) for p in ring]).buffer(20.0))
    for furn in room.furniture:
        if furn.furniture_type == "wardrobe" and len(furn.polygon_mm) >= 3:
            forbidden_parts.append(
                Polygon([(p.x_mm, p.y_mm) for p in furn.polygon_mm]).buffer(220.0)
            )

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
    *,
    brief: ResidentialSystemsBrief | None = None,
    meter_override: Point2D | None = None,
    earth_pit_override: Point2D | None = None,
    telecom_override: Point2D | None = None,
) -> tuple[list[PlacedDevice], list[ReviewFlag]]:
    """Deterministically place luminaires, switches, sockets, A/C, ELV, plant & site markers."""
    active_brief = brief or ResidentialSystemsBrief()
    tmpl_std = standards.room_templates
    wz_by_room = {w.room_id: w for w in wet_zones}

    # Exact building outer boundary via unary_union (accurate on L- and U-shaped floorplans)
    all_room_polys = [Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm]) for r in building.rooms]
    outer_ring: BaseGeometry = unary_union(all_room_polys).boundary

    devices: list[PlacedDevice] = []
    flags: list[ReviewFlag] = []
    counters: dict[str, int] = {
        "L": 0,
        "PD": 0,
        "ML": 0,
        "EF": 0,
        "SW": 0,
        "DIM": 0,
        "FI": 0,
        "S": 0,
        "AC": 0,
        "APP": 0,
        "CCU": 0,
        "WHDP": 0,
        "MTR": 0,
        "EP": 0,
    }

    def _next_dev(prefix: str) -> str:
        counters[prefix] += 1
        return f"DEV-{prefix}{counters[prefix]}"

    def _offset_along_wall(pt: Point2D, angle_deg: float, delta_mm: float) -> Point2D:
        rad = math.radians(angle_deg)
        return Point2D(
            x_mm=round(pt.x_mm + delta_mm * math.cos(rad), 1),
            y_mm=round(pt.y_mm + delta_mm * math.sin(rad), 1),
        )

    emitted_appliance_keys: set[str] = set()

    for room in building.rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        room_wz = wz_by_room.get(room.id)
        rpoly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
        target_lux, lum_w = _ROOM_LIGHTING_SPECS.get(room.normalized_type, (150, 15.0))

        # Match both doors owned by this room AND outward-swinging doors touching its perimeter
        room_doors = [
            d
            for d in building.doors
            if d.room_id == room.id
            or float(rpoly.exterior.distance(Point(d.hinge.x_mm, d.hinge.y_mm))) <= 120.0
        ]
        room_windows = [w for w in building.windows if w.room_id == room.id]

        # Valid walls strictly excluding doors, swings, windows, wardrobes & Sec. 701 wet zones
        valid_walls = compute_valid_wall_segments(
            room,
            doors=building.doors,  # check all doors touching this room's boundary
            windows=room_windows,
            wet_zone=room_wz,
            building_outer_ring=outer_ring,
            exclude_wet_zones=True,
        )

        # 1. Ceiling Luminaires (+ Lumen Method sizing, fixture types & dining pendant snapping)
        min_lums_by_lux = max(
            1,
            int(math.ceil((target_lux * room.area_m2) / max(lum_w * 105.0 * 0.65 * 0.80, 100.0))),
        )
        req_lum_count = min(12, max(1, rt.min_lighting_points, min_lums_by_lux))
        lum_pts = _place_ceiling_luminaires(room, req_lum_count)
        is_large_split = (
            room.area_m2 >= 22.0
            and len(lum_pts) >= 2
            and room.normalized_type
            in (
                RoomType.LIVING_ROOM,
                RoomType.KITCHEN_LIVING,
                RoomType.OPEN_OFFICE,
                RoomType.RETAIL_FLOOR,
            )
        )
        room_lum_ids: list[str] = []
        room_lum_group_a: list[str] = []
        room_lum_group_b: list[str] = []
        split_idx = max(1, len(lum_pts) // 2)

        for idx, lpt in enumerate(lum_pts, start=1):
            zone = classify_point_wet_zone(lpt, room_wz)
            if room.is_outdoor:
                ip = "IP65"
                fix_type = "IP65 LED Outdoor Bulkhead"
            elif zone in ("zone_0", "zone_1", "zone_2") or room.is_wet_room:
                ip = "IP44"
                fix_type = "IP44 Recessed LED Downlight"
            elif room.normalized_type in (RoomType.OPEN_OFFICE, RoomType.RETAIL_FLOOR):
                ip = "IP20"
                fix_type = "600x600 LED Panel (UGR<19)"
            elif room.normalized_type in (RoomType.KITCHEN, RoomType.UTILITY):
                ip = "IP20"
                fix_type = "4000K Recessed LED Downlight"
            else:
                ip = "IP20"
                fix_type = "3000K Recessed LED Downlight"

            grp = "b" if (is_large_split and idx > split_idx) else "a"
            lid = _next_dev("L")
            room_lum_ids.append(lid)
            if grp == "b":
                room_lum_group_b.append(lid)
            else:
                room_lum_group_a.append(lid)

            devices.append(
                PlacedDevice(
                    device_id=lid,
                    room_id=room.id,
                    device_type=DeviceType.LUMINAIRE,
                    label=f"Luminaire {idx} ({room.raw_label})",
                    location_mm=lpt,
                    mounting_height_mm=room.ceiling_height_mm,
                    ip_rating=ip,
                    switch_group=grp,
                    luminaire_wattage_w=lum_w,
                    luminaire_fixture_type=fix_type,
                    lumens=int(round(lum_w * 105.0)),
                    target_lux=target_lux,
                )
            )

        # Furniture-aware Feature Pendant snapped over detected dining table
        dining_furn = next(
            (fn for fn in room.furniture if fn.furniture_type == "dining_table"), None
        )
        if dining_furn is not None:
            pid = _next_dev("PD")
            room_lum_ids.append(pid)
            room_lum_group_b.append(pid)
            devices.append(
                PlacedDevice(
                    device_id=pid,
                    room_id=room.id,
                    device_type=DeviceType.PENDANT_LIGHT,
                    label=f"Dining Feature Pendant ({room.raw_label})",
                    location_mm=dining_furn.centroid_mm,
                    mounting_height_mm=room.ceiling_height_mm,
                    ip_rating="IP20",
                    switch_group="b",
                    luminaire_wattage_w=18.0,
                    luminaire_fixture_type="3000K Feature Pendant",
                    lumens=1900,
                    target_lux=target_lux,
                )
            )

        # 1b. Wet-room Cyprus accessories: IP44 Vanity Mirror Light + Exhaust Fan + 3P Fan Isolator
        mirror_id: str | None = None
        fan_id: str | None = None
        if room.is_wet_room:
            # Mirror light near washbasin if present, else room boundary midpoint outside Zone 1
            basins = [f for f in room.fixtures if str(f.fixture_type) == "basin"]
            if basins:
                bpt = Polygon([(p.x_mm, p.y_mm) for p in basins[0].polygon_mm]).centroid
                wall_pt = nearest_points(rpoly.exterior, bpt)[0]
                ml_loc = Point2D(x_mm=round(float(wall_pt.x), 1), y_mm=round(float(wall_pt.y), 1))
            else:
                wp = rpoly.exterior.interpolate(0.25, normalized=True)
                ml_loc = Point2D(x_mm=round(float(wp.x), 1), y_mm=round(float(wp.y), 1))
            mirror_id = _next_dev("ML")
            devices.append(
                PlacedDevice(
                    device_id=mirror_id,
                    room_id=room.id,
                    device_type=DeviceType.MIRROR_LIGHT,
                    label=f"IP44 Vanity Mirror Light ({room.raw_label})",
                    location_mm=ml_loc,
                    mounting_height_mm=2000.0,
                    ip_rating="IP44",
                    switch_group="b",
                    luminaire_wattage_w=10.0,
                    target_lux=200,
                )
            )

            # Exhaust fan inside wet room near exterior wall (or upper corner)
            minx, miny, maxx, maxy = rpoly.bounds
            fan_cand = Point(minx + 0.80 * (maxx - minx), miny + 0.80 * (maxy - miny))
            if not rpoly.contains(fan_cand):
                fan_cand = rpoly.representative_point()
            fan_id = _next_dev("EF")
            devices.append(
                PlacedDevice(
                    device_id=fan_id,
                    room_id=room.id,
                    device_type=DeviceType.EXHAUST_FAN,
                    label=f"Bathroom Exhaust Fan ({room.raw_label})",
                    location_mm=Point2D(
                        x_mm=round(float(fan_cand.x), 1), y_mm=round(float(fan_cand.y), 1)
                    ),
                    mounting_height_mm=2400.0,
                    ip_rating="IP44",
                    switch_group="a",
                    luminaire_wattage_w=20.0,
                )
            )

        # 2. Room Light Switch(es) with deterministic 1G1W / 2G1W / 1G2W (2-way) control mapping
        sw_pt, sw_ang = _place_room_switch(room, room_doors, valid_walls, room_wz)
        sw_ip = "IP65" if room.is_outdoor else "IP20"

        is_master_bed = room.normalized_type == RoomType.MASTER_BEDROOM or (
            room.normalized_type == RoomType.BEDROOM
            and any(tok in room.raw_label.lower() for tok in ("master", "κυριο", "κύριο", "main"))
        )
        rminx, rminy, rmaxx, rmaxy = rpoly.bounds
        room_span_mm = max(rmaxx - rminx, rmaxy - rminy)
        is_two_way_circulation = room.normalized_type in (
            RoomType.CORRIDOR,
            RoomType.STAIRWELL,
        ) and (room_span_mm >= 3500.0 or len(room_doors) >= 2)

        if room.is_wet_room:
            ctrl_ids = (
                room_lum_group_a + ([fan_id] if fan_id else []) + ([mirror_id] if mirror_id else [])
            )
            devices.append(
                PlacedDevice(
                    device_id=_next_dev("SW"),
                    room_id=room.id,
                    device_type=DeviceType.SWITCH,
                    label=f"2G1W Light/Mirror Switch ({room.raw_label})",
                    location_mm=sw_pt,
                    mounting_height_mm=1200.0,
                    wall_angle_deg=sw_ang,
                    ip_rating=sw_ip,
                    switch_group="a,b",
                    switch_type="2G1W",
                    controlled_device_ids=ctrl_ids,
                )
            )
            # 3-Pole Fan Isolator outside wet room beside switch at H=+2200mm
            if fan_id is not None:
                fi_cand = _offset_along_wall(sw_pt, sw_ang, 220.0)
                if rpoly.contains(Point(fi_cand.x_mm, fi_cand.y_mm)):
                    fi_cand = _offset_along_wall(sw_pt, sw_ang, -220.0)
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("FI"),
                        room_id=room.id,
                        device_type=DeviceType.FAN_ISOLATOR_3P,
                        label=f"3-Pole Fan Isolator ({room.raw_label})",
                        location_mm=fi_cand,
                        mounting_height_mm=2200.0,
                        wall_angle_deg=sw_ang,
                        ip_rating="IP20",
                        switch_group="a",
                        switch_type="3P-ISOL",
                        controlled_device_ids=[fan_id],
                    )
                )
        elif is_large_split:
            is_dimmable_living = room.normalized_type in (
                RoomType.LIVING_ROOM,
                RoomType.KITCHEN_LIVING,
            )
            devices.append(
                PlacedDevice(
                    device_id=_next_dev("DIM" if is_dimmable_living else "SW"),
                    room_id=room.id,
                    device_type=(
                        DeviceType.DIMMER_SWITCH if is_dimmable_living else DeviceType.SWITCH
                    ),
                    label=(
                        f"2G1W LED Dimmer/Zone Switch ({room.raw_label})"
                        if is_dimmable_living
                        else f"2G1W Zone Switch ({room.raw_label})"
                    ),
                    location_mm=sw_pt,
                    mounting_height_mm=1200.0,
                    wall_angle_deg=sw_ang,
                    ip_rating=sw_ip,
                    switch_group="a,b",
                    switch_type="2G1W",
                    controlled_device_ids=room_lum_ids,
                )
            )
            # Add 3-way intermediate / secondary entry switch in large rooms (>=30 m² or >=3 doors)
            if (room.area_m2 >= 30.0 or len(room_doors) >= 3) and valid_walls:
                far_cand = max(
                    valid_walls,
                    key=lambda c: Point(sw_pt.x_mm, sw_pt.y_mm).distance(
                        c.line.interpolate(0.5, normalized=True)
                    ),
                )
                far_pt = far_cand.line.interpolate(0.5, normalized=True)
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("SW"),
                        room_id=room.id,
                        device_type=DeviceType.SWITCH,
                        label=f"1G-INT 3-Way Intermediate Switch ({room.raw_label})",
                        location_mm=Point2D(
                            x_mm=round(float(far_pt.x), 1), y_mm=round(float(far_pt.y), 1)
                        ),
                        mounting_height_mm=1200.0,
                        wall_angle_deg=far_cand.angle_deg,
                        ip_rating=sw_ip,
                        switch_group="a",
                        switch_type="1G2W",
                        controlled_device_ids=room_lum_group_a or room_lum_ids,
                    )
                )
        elif is_two_way_circulation or is_master_bed:
            # Primary 2-way switch at room entry door
            devices.append(
                PlacedDevice(
                    device_id=_next_dev("SW"),
                    room_id=room.id,
                    device_type=DeviceType.SWITCH,
                    label=f"1G2W Entry Switch ({room.raw_label})",
                    location_mm=sw_pt,
                    mounting_height_mm=1200.0,
                    wall_angle_deg=sw_ang,
                    ip_rating=sw_ip,
                    switch_group="a",
                    switch_type="1G2W",
                    controlled_device_ids=room_lum_ids,
                )
            )
            # Secondary 2-way switch snapped to detected bed headboard wall or far wall
            bed_furn = next((fn for fn in room.furniture if fn.furniture_type == "bed"), None)
            if is_master_bed and bed_furn is not None:
                proj = nearest_points(
                    rpoly.exterior, Point(bed_furn.centroid_mm.x_mm, bed_furn.centroid_mm.y_mm)
                )[0]
                sw2_loc = Point2D(x_mm=round(float(proj.x), 1), y_mm=round(float(proj.y), 1))
                sw2_ang = sw_ang
            elif valid_walls:
                far_cand = max(
                    valid_walls,
                    key=lambda c: Point(sw_pt.x_mm, sw_pt.y_mm).distance(
                        c.line.interpolate(0.5, normalized=True)
                    ),
                )
                far_pt = far_cand.line.interpolate(0.5, normalized=True)
                sw2_loc = Point2D(x_mm=round(float(far_pt.x), 1), y_mm=round(float(far_pt.y), 1))
                sw2_ang = far_cand.angle_deg
            else:
                sw2_loc = _offset_along_wall(sw_pt, sw_ang, 1200.0)
                sw2_ang = sw_ang
            devices.append(
                PlacedDevice(
                    device_id=_next_dev("SW"),
                    room_id=room.id,
                    device_type=DeviceType.SWITCH,
                    label=(
                        f"1G2W Bedside Switch ({room.raw_label})"
                        if is_master_bed
                        else f"1G2W Far-End Switch ({room.raw_label})"
                    ),
                    location_mm=sw2_loc,
                    mounting_height_mm=750.0 if is_master_bed else 1200.0,
                    wall_angle_deg=sw2_ang,
                    ip_rating=sw_ip,
                    switch_group="a",
                    switch_type="1G2W",
                    controlled_device_ids=room_lum_ids,
                )
            )
            if is_two_way_circulation and len(room_doors) >= 3 and len(valid_walls) >= 2:
                mid_cand = valid_walls[len(valid_walls) // 2]
                mid_pt = mid_cand.line.interpolate(0.5, normalized=True)
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("SW"),
                        room_id=room.id,
                        device_type=DeviceType.SWITCH,
                        label=f"1G-INT 3-Way Intermediate Switch ({room.raw_label})",
                        location_mm=Point2D(
                            x_mm=round(float(mid_pt.x), 1), y_mm=round(float(mid_pt.y), 1)
                        ),
                        mounting_height_mm=1200.0,
                        wall_angle_deg=mid_cand.angle_deg,
                        ip_rating=sw_ip,
                        switch_group="a",
                        switch_type="1G2W",
                        controlled_device_ids=room_lum_ids,
                    )
                )
        else:
            use_dimmer = room.normalized_type == RoomType.LIVING_ROOM
            devices.append(
                PlacedDevice(
                    device_id=_next_dev("DIM" if use_dimmer else "SW"),
                    room_id=room.id,
                    device_type=DeviceType.DIMMER_SWITCH if use_dimmer else DeviceType.SWITCH,
                    label=(
                        f"1G-DIM LED Dimmer Switch ({room.raw_label})"
                        if use_dimmer
                        else f"1G1W Light Switch ({room.raw_label})"
                    ),
                    location_mm=sw_pt,
                    mounting_height_mm=1200.0,
                    wall_angle_deg=sw_ang,
                    ip_rating=sw_ip,
                    switch_group="a",
                    switch_type="1G1W",
                    controlled_device_ids=room_lum_ids,
                )
            )

        # 3. Twin 13A Socket Outlets (furniture-aware bedside snapping; never in wet-zone excl.)
        if rt.socket_count_default > 0 and rt.socket_group != "none":
            bed_furn_s = next((fn for fn in room.furniture if fn.furniture_type == "bed"), None)
            furn_sock_pts: list[tuple[Point2D, float]] = []
            if bed_furn_s is not None and room.normalized_type in (
                RoomType.MASTER_BEDROOM,
                RoomType.BEDROOM,
            ):
                proj = nearest_points(
                    rpoly.exterior,
                    Point(bed_furn_s.centroid_mm.x_mm, bed_furn_s.centroid_mm.y_mm),
                )[0]
                bwall = Point2D(x_mm=round(float(proj.x), 1), y_mm=round(float(proj.y), 1))
                furn_sock_pts.append((_offset_along_wall(bwall, sw_ang, -950.0), sw_ang))
                furn_sock_pts.append((_offset_along_wall(bwall, sw_ang, 950.0), sw_ang))

            rem_count = max(0, rt.socket_count_default - len(furn_sock_pts))
            sock_pts = furn_sock_pts + _place_points_along_candidates(valid_walls, rem_count)
            mount_h = 1150.0 if room.normalized_type.value in ("kitchen", "utility") else 450.0
            sock_ip = "IP65" if room.is_outdoor else "IP20"
            for idx, (spt, sang) in enumerate(sock_pts, start=1):
                is_bedside = idx <= len(furn_sock_pts)
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("S"),
                        room_id=room.id,
                        device_type=DeviceType.SOCKET_TWIN,
                        label=(
                            f"Bedside Twin 13A Socket {idx} ({room.raw_label})"
                            if is_bedside
                            else f"Twin 13A Socket {idx} ({room.raw_label})"
                        ),
                        location_mm=spt,
                        mounting_height_mm=650.0 if is_bedside else mount_h,
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

        # 4. Split A/C Unit & Isolator (only when commercial OR residential hvac_mode == split_ac)
        if (
            rt.has_split_ac
            and rt.ac_electrical_w > 0.0
            and (is_commercial_building(building) or active_brief.hvac_mode == "split_ac")
        ):
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
                    label=f"20A DP Split A/C Isolator ({room.raw_label})",
                    location_mm=ac_loc,
                    mounting_height_mm=2200.0,
                    wall_angle_deg=ac_ang,
                    ip_rating="IP20",
                )
            )

        # 5. Dedicated Appliance Outlets + Cyprus Control Switches (45A CCU, 20A DP WH)
        is_comm = is_commercial_building(building)
        room_apps = [
            a
            for a in rt.dedicated_appliances
            if a.key not in emitted_appliance_keys
            and not (
                is_comm
                and room.normalized_type == RoomType.KITCHEN
                and a.category.value in ("cooker", "oven")
            )
        ]
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
                    app_height = 450.0
                elif app.category.value == "oven":
                    dtype = DeviceType.OVEN_OUTLET
                    app_height = 450.0
                elif app.category.value == "water_heater":
                    dtype = DeviceType.WATER_HEATER_ISOLATOR
                    app_height = 1600.0
                else:
                    dtype = DeviceType.APPLIANCE_OUTLET
                    app_height = 450.0

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

                # Pair Cyprus 45A DP Cooker Control Unit (H=+1150mm) beside cooker outlet
                if dtype == DeviceType.COOKER_OUTLET:
                    ccu_loc = _offset_along_wall(loc, ang, 260.0)
                    devices.append(
                        PlacedDevice(
                            device_id=_next_dev("CCU"),
                            room_id=room.id,
                            device_type=DeviceType.COOKER_CONTROL_UNIT,
                            label=f"45A DP Cooker Control Unit ({room.raw_label})",
                            location_mm=ccu_loc,
                            mounting_height_mm=1150.0,
                            wall_angle_deg=ang,
                            ip_rating="IP20",
                            appliance_key=app.key,
                        )
                    )
                # Pair Cyprus 20A DP Switch + Neon (H=+1200mm) beside solar/immersion WH outlet
                elif dtype == DeviceType.WATER_HEATER_ISOLATOR:
                    dp_loc = _offset_along_wall(loc, ang, 260.0)
                    devices.append(
                        PlacedDevice(
                            device_id=_next_dev("WHDP"),
                            room_id=room.id,
                            device_type=DeviceType.WATER_HEATER_DP_SWITCH,
                            label=f"20A DP Water Heater Switch + Neon ({room.raw_label})",
                            location_mm=dp_loc,
                            mounting_height_mm=1200.0,
                            wall_angle_deg=ang,
                            ip_rating="IP20",
                            appliance_key=app.key,
                        )
                    )

    # 6. EAC Meter Cabinet & TT Earth Inspection Pit Site Markers on exterior wall nearest DB
    if building.rooms:
        if building.sub_boards:
            primary_floor = next(
                (sb.floor for sb in building.sub_boards if sb.floor == "GF"),
                building.sub_boards[0].floor,
            )
            primary_rooms = [
                r for r in building.rooms if r.floor == primary_floor
            ] or building.rooms
            primary_polys = [
                Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm]) for r in primary_rooms
            ]
            site_union = unary_union(primary_polys)
            site_outer = site_union.boundary
            site_centroid = site_union.centroid
            ref_room_id = primary_rooms[0].id
        else:
            site_outer = outer_ring
            site_centroid = unary_union(all_room_polys).centroid
            ref_room_id = building.rooms[0].id

        db_pt = Point(building.db_anchor.location_mm.x_mm, building.db_anchor.location_mm.y_mm)
        ext_pt = nearest_points(site_outer, db_pt)[0]
        out_dx = float(ext_pt.x) - float(site_centroid.x)
        out_dy = float(ext_pt.y) - float(site_centroid.y)
        out_norm = max(math.hypot(out_dx, out_dy), 1.0)
        ux, uy = out_dx / out_norm, out_dy / out_norm
        mtr_loc = (
            Point2D(x_mm=round(meter_override.x_mm, 1), y_mm=round(meter_override.y_mm, 1))
            if meter_override is not None
            else Point2D(
                x_mm=round(float(ext_pt.x) + ux * 180.0, 1),
                y_mm=round(float(ext_pt.y) + uy * 180.0, 1),
            )
        )
        pit_loc = (
            Point2D(x_mm=round(earth_pit_override.x_mm, 1), y_mm=round(earth_pit_override.y_mm, 1))
            if earth_pit_override is not None
            else Point2D(
                x_mm=round(float(ext_pt.x) + ux * 700.0 - uy * 350.0, 1),
                y_mm=round(float(ext_pt.y) + uy * 700.0 + ux * 350.0, 1),
            )
        )
        devices.append(
            PlacedDevice(
                device_id=_next_dev("MTR"),
                room_id=ref_room_id,
                device_type=DeviceType.EAC_METER_CABINET,
                label="EAC Meter Cabinet (Verify Boundary on Site Plan)",
                location_mm=mtr_loc,
                mounting_height_mm=1500.0,
                ip_rating="IP65",
            )
        )
        devices.append(
            PlacedDevice(
                device_id=_next_dev("EP"),
                room_id=ref_room_id,
                device_type=DeviceType.EARTH_PIT,
                label="TT Earth Pit (Cu Rod Ø16x1200mm)",
                location_mm=pit_loc,
                mounting_height_mm=0.0,
                ip_rating="IP68",
            )
        )
        flags.append(
            ReviewFlag(
                code="EAC_METER_AND_EARTH_PIT_SITE_VERIFY",
                severity=ReviewSeverity.INFO,
                category="eac",
                regulation_ref="Cyprus EAC Metering & TT Earthing Requirements",
                message=(
                    "EAC Meter Cabinet and TT Earth Inspection Pit markers are placed adjacent to "
                    "the nearest exterior wall; confirm exact plot-boundary coordinates on the "
                    "official Land Registry cadastral site plan."
                ),
                requires_etek_signoff=True,
            )
        )

    # 7. Stage 5 Residential Systems (ELV, Security, Smoke/Heat, Shutters, Plant, KNX, Site)
    s5_devs, s5_flags = place_stage5_residential_systems(
        building,
        devices,
        active_brief,
        telecom_override=telecom_override,
    )
    devices.extend(s5_devs)
    flags.extend(s5_flags)

    return devices, flags


def build_switch_legs(devices: list[PlacedDevice]) -> list[SwitchLeg]:
    """Build deterministic SwitchLeg control links from circuit-bound switches & fan isolators."""
    legs: list[SwitchLeg] = []
    for dev in devices:
        if dev.device_type not in (
            DeviceType.SWITCH,
            DeviceType.DIMMER_SWITCH,
            DeviceType.FAN_ISOLATOR_3P,
        ):
            continue
        if not dev.controlled_device_ids:
            continue
        st = dev.switch_type or "1G1W"
        legs.append(
            SwitchLeg(
                switch_id=dev.device_id,
                room_id=dev.room_id,
                group_label=dev.switch_group or "a",
                switch_type=st,
                is_two_way="2W" in st,
                target_device_ids=list(dev.controlled_device_ids),
                circuit_id=dev.circuit_id,
            )
        )
    return legs
