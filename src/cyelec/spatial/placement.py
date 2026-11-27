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
from cyelec.solver.residential_engineering import size_room_luminaires
from cyelec.spatial.stage5_placement import place_stage5_residential_systems
from cyelec.spatial.wet_zones import classify_point_wet_zone, is_point_in_socket_exclusion
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
    w = max(maxx - minx, 1.0)
    h = max(maxy - miny, 1.0)
    if count <= 3:
        cols = count if w >= h else 1
        rows = 1 if w >= h else count
    else:
        aspect = w / h
        cols = max(2, min(count, int(round(math.sqrt(count * aspect)))))
        rows = int(math.ceil(count / cols))
        while cols * rows < count:
            rows += 1

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


def _plan_uniformity_fill_points(
    room: Room,
    ceiling_lums: list[PlacedDevice],
    *,
    max_extra: int,
    wall_inset_mm: float = 600.0,
    min_spacing_mm: float = 900.0,
) -> tuple[list[Point2D], float, float]:
    """Greedy closed-loop BS EN 12464-1 uniformity refinement (U0 = E_min / E_avg).

    Repeatedly evaluates the true 2D working-plane grid and drops one fill downlight at the
    darkest grid point (pulled >= ``wall_inset_mm`` off the walls and >= ``min_spacing_mm`` from
    every existing fixture) until U0 meets the room target or ``max_extra`` is reached.

    Because every term of the grid model (direct + Sumpner inter-reflection) is linear in
    luminous flux, U0 is invariant to a uniform lumen rescale; fixing U0 geometrically here lets
    the caller rescale the lumen package afterwards to land E_avg on target without wasted watts.

    Returns ``(fill_points, u0_before, u0_after)``; ``fill_points`` is empty unless U0 improved.
    """
    from cyelec.solver.photometrics import evaluate_room_photometrics_2d

    if not ceiling_lums or max_extra <= 0 or room.area_m2 < 4.0:
        return [], 0.0, 0.0

    poly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
    inner: BaseGeometry = poly.buffer(-wall_inset_mm)
    if inner.is_empty or inner.area < 0.15 * poly.area:
        inner = poly.buffer(-min(300.0, wall_inset_mm))
    if inner.is_empty:
        inner = poly

    base = evaluate_room_photometrics_2d(room, ceiling_lums, grid_step_mm=250.0)
    u0_before = base.uniformity_u0
    if base.u0_compliant or base.grid_points == 0:
        return [], u0_before, u0_before

    template = ceiling_lums[0]
    trial = list(ceiling_lums)
    fills: list[Point2D] = []
    res = base
    for _ in range(max_extra):
        if res.u0_compliant or res.grid_points == 0:
            break
        order = sorted(range(len(res.lux_values)), key=lambda k: res.lux_values[k])
        placed = False
        for k in order[:80]:
            cand = Point(res.xs_mm[k], res.ys_mm[k])
            if not inner.contains(cand):
                cand = nearest_points(inner, cand)[0]
            if not poly.contains(cand):
                continue
            if any(
                math.hypot(cand.x - d.location_mm.x_mm, cand.y - d.location_mm.y_mm)
                < min_spacing_mm
                for d in trial
            ):
                continue
            new_pt = Point2D(x_mm=round(float(cand.x), 1), y_mm=round(float(cand.y), 1))
            trial.append(
                template.model_copy(
                    update={"device_id": f"_u0fill{len(fills)}", "location_mm": new_pt}
                )
            )
            fills.append(new_pt)
            placed = True
            break
        if not placed:
            break
        res = evaluate_room_photometrics_2d(room, trial, grid_step_mm=250.0)

    if not fills or res.uniformity_u0 <= u0_before + 1e-6:
        return [], u0_before, u0_before
    return fills, u0_before, res.uniformity_u0


def _snap_to_room_edge(
    room: Room,
    target: Point2D,
    *,
    inset_margin_mm: float = 200.0,
) -> tuple[Point2D, float, LineString]:
    """Project a point onto the closest room polygon edge and return (point, edge_angle, edge)."""
    pts = room.polygon_mm
    n = len(pts)
    tp = Point(target.x_mm, target.y_mm)
    best_dist = float("inf")
    best_edge = LineString([(pts[0].x_mm, pts[0].y_mm), (pts[1 % n].x_mm, pts[1 % n].y_mm)])
    best_ang = 0.0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        edge = LineString([(a.x_mm, a.y_mm), (b.x_mm, b.y_mm)])
        if edge.length < 50.0:
            continue
        d = float(edge.distance(tp))
        if d < best_dist:
            best_dist = d
            best_edge = edge
            best_ang = round(math.degrees(math.atan2(b.y_mm - a.y_mm, b.x_mm - a.x_mm)) % 360.0, 1)
    proj_d = float(best_edge.project(tp))
    lo_d = min(inset_margin_mm, best_edge.length * 0.45)
    hi_d = max(best_edge.length - inset_margin_mm, best_edge.length * 0.55)
    clamped_d = min(max(proj_d, lo_d), hi_d)
    snapped = best_edge.interpolate(clamped_d)
    pt = Point2D(x_mm=round(float(snapped.x), 1), y_mm=round(float(snapped.y), 1))
    return pt, best_ang, best_edge


def _has_separated_doors(room_doors: list[DoorOpening], min_sep_mm: float = 1800.0) -> bool:
    """Return True when a room has >= 2 doorways separated by at least min_sep_mm."""
    for i, d1 in enumerate(room_doors):
        for d2 in room_doors[i + 1 :]:
            dist_mm = math.hypot(d1.hinge.x_mm - d2.hinge.x_mm, d1.hinge.y_mm - d2.hinge.y_mm)
            if dist_mm >= min_sep_mm:
                return True
    return False


def _place_room_switch(
    room: Room,
    room_doors: list[DoorOpening],
    valid_walls: list[_WallSegmentCandidate],
    wet_zone: RoomWetZones | None,
    *,
    indoor_union: BaseGeometry | None = None,
) -> tuple[Point2D, float]:
    """Place light switch on latch side of entry door (outside wet rooms; indoors for verandas)."""
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

        # For wet rooms & outdoor verandas with a doorway, offset 120 mm into the indoor side
        if room.is_wet_room or room.is_outdoor:
            nx, ny = -math.sin(rad), math.cos(rad)
            cand_a = Point(sx + 120.0 * nx, sy + 120.0 * ny)
            cand_b = Point(sx - 120.0 * nx, sy - 120.0 * ny)
            if indoor_union is not None and not indoor_union.is_empty:
                a_in = indoor_union.contains(cand_a)
                b_in = indoor_union.contains(cand_b)
                if a_in and not b_in:
                    chosen = cand_a
                elif b_in and not a_in:
                    chosen = cand_b
                else:
                    chosen = (
                        cand_a
                        if float(indoor_union.distance(cand_a))
                        <= float(indoor_union.distance(cand_b))
                        else cand_b
                    )
            else:
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

    # For outdoor verandas without a hinged door symbol (e.g. sliding terrace doors),
    # anchor the switch strictly indoors on the shared wall with the adjacent indoor room!
    if room.is_outdoor and indoor_union is not None and not indoor_union.is_empty:
        shared = boundary.intersection(indoor_union.buffer(80.0))
        shared_lines = _extract_linestrings(shared)
        if shared_lines:
            best_seg = max(shared_lines, key=lambda ls: ls.length)
            c0 = best_seg.coords[0]
            c1 = best_seg.coords[-1]
            seg_ang = round(math.degrees(math.atan2(c1[1] - c0[1], c1[0] - c0[0])) % 360.0, 1)
            mid = best_seg.interpolate(0.35, normalized=True)
            rad = math.radians(seg_ang)
            nx, ny = -math.sin(rad), math.cos(rad)
            cand_a = Point(mid.x + 140.0 * nx, mid.y + 140.0 * ny)
            cand_b = Point(mid.x - 140.0 * nx, mid.y - 140.0 * ny)
            chosen = (
                cand_a
                if float(indoor_union.distance(cand_a)) <= float(indoor_union.distance(cand_b))
                else cand_b
            )
            return (
                Point2D(x_mm=round(float(chosen.x), 1), y_mm=round(float(chosen.y), 1)),
                seg_ang,
            )

    if valid_walls:
        # Prefer interior partition walls over exterior window/railing walls when no door is matched
        interior_walls = [w for w in valid_walls if not w.is_exterior]
        chosen_wall = interior_walls[0] if interior_walls else valid_walls[0]
        pt = chosen_wall.line.interpolate(0.18, normalized=True)
        return (
            Point2D(x_mm=round(float(pt.x), 1), y_mm=round(float(pt.y), 1)),
            chosen_wall.angle_deg,
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
    indoor_polys = [
        Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm])
        for r in building.rooms
        if not r.is_outdoor and not r.is_wet_room
    ]
    indoor_union: BaseGeometry = unary_union(indoor_polys) if indoor_polys else Polygon()

    devices: list[PlacedDevice] = []
    flags: list[ReviewFlag] = []
    counters: dict[str, int] = {
        "L": 0,
        "PD": 0,
        "WS": 0,
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
        counters[prefix] = counters.get(prefix, 0) + 1
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
        req_lum_count, lum_w, lum_lm, target_lux = size_room_luminaires(
            room.normalized_type,
            room.area_m2,
            room.ceiling_height_mm,
            min_points=rt.min_lighting_points,
        )

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
                    lumens=lum_lm,
                    target_lux=target_lux,
                )
            )

        # Closed-loop BS EN 12464-1 uniformity refinement: if the 2D grid shows a dark corner /
        # L-shaped leg (U0 = E_min/E_avg below target), add fill downlights at the darkest
        # working-plane points. U0 is flux-invariant, so the lumen calibration below then
        # rescales the whole room package (up OR down) to land E_avg on target.
        room_ceils = [d for d in devices if d.device_id in room_lum_ids]
        n_u0_fills = 0
        if room_ceils:
            max_fill = min(8, max(2, len(room_ceils) // 2))
            fill_pts, u0_before, u0_after = _plan_uniformity_fill_points(
                room, room_ceils, max_extra=max_fill
            )
            for fpt in fill_pts:
                n_u0_fills += 1
                f_zone = classify_point_wet_zone(fpt, room_wz)
                f_tmpl = room_ceils[0]
                f_ip = f_tmpl.ip_rating
                f_type = f_tmpl.luminaire_fixture_type
                if not room.is_outdoor and (
                    f_zone in ("zone_0", "zone_1", "zone_2") or room.is_wet_room
                ):
                    f_ip, f_type = "IP44", "IP44 Recessed LED Downlight"
                nearest = min(
                    room_ceils,
                    key=lambda d: math.hypot(
                        d.location_mm.x_mm - fpt.x_mm, d.location_mm.y_mm - fpt.y_mm
                    ),
                )
                f_grp = nearest.switch_group or "a"
                fid = _next_dev("L")
                room_lum_ids.append(fid)
                (room_lum_group_b if f_grp == "b" else room_lum_group_a).append(fid)
                devices.append(
                    PlacedDevice(
                        device_id=fid,
                        room_id=room.id,
                        device_type=DeviceType.LUMINAIRE,
                        label=f"Luminaire {len(lum_pts) + n_u0_fills} ({room.raw_label})",
                        location_mm=fpt,
                        mounting_height_mm=room.ceiling_height_mm,
                        ip_rating=f_ip,
                        switch_group=f_grp,
                        luminaire_wattage_w=lum_w,
                        luminaire_fixture_type=f_type,
                        lumens=lum_lm,
                        target_lux=target_lux,
                    )
                )
            if n_u0_fills:
                flags.append(
                    ReviewFlag(
                        code="PHOTOMETRIC_UNIFORMITY_FILL_ADDED",
                        severity=ReviewSeverity.INFO,
                        category="geometry",
                        regulation_ref="BS EN 12464-1 (Illuminance Uniformity U0 = Emin/Eavg)",
                        message=(
                            f"Room '{room.raw_label}' ({room.area_m2:.1f} m²): added "
                            f"{n_u0_fills} fill downlight(s) at darkest 2D grid points; "
                            f"ceiling-only U0 {u0_before:.2f} -> {u0_after:.2f} "
                            "(lumen package re-calibrated to target E_avg)."
                        ),
                        requires_etek_signoff=False,
                    )
                )

        # Closed-loop 2D working-plane photometric calibration (DIALux-style):
        # verify the placed ceiling luminaires achieve target_lux under true 2D point-by-point
        # inverse-square cosine-cubed + cavity inter-reflection physics, stepping up fixture
        # lumen package cleanly (105 lm/W LED) if direct geometry under-illuminates the plane.
        room_ceils = [d for d in devices if d.device_id in room_lum_ids]
        if room_ceils:
            from cyelec.solver.photometrics import evaluate_room_photometrics_2d

            grid_chk = evaluate_room_photometrics_2d(room, room_ceils, grid_step_mm=350.0)
            scale_req = 1.0
            if 0.0 < grid_chk.avg_lux < target_lux * 0.98:
                scale_req = min(3.2, (target_lux * 1.04) / grid_chk.avg_lux)
            elif n_u0_fills and grid_chk.avg_lux > target_lux * 1.10:
                # Fill fixtures raised E_avg; trim the per-fixture package back to target
                # (never below 300 lm) so uniformity is fixed without wasted circuit load.
                scale_req = max(300.0 / lum_lm, (target_lux * 1.04) / grid_chk.avg_lux)
            if abs(scale_req - 1.0) > 1e-6:
                new_lm = int(math.ceil((lum_lm * scale_req) / 50.0) * 50)
                new_w = round(new_lm / 105.0, 1)
                for idx_d, dev in enumerate(devices):
                    if dev.device_id in room_lum_ids:
                        devices[idx_d] = dev.model_copy(
                            update={"lumens": new_lm, "luminaire_wattage_w": new_w}
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

        # Opt-in Cyprus decorative wall sconces (φωτιστικά τοίχου) for bedrooms & living/dining
        if (
            brief is not None
            and brief.include_decorative_wall_sconces
            and not room.is_wet_room
            and not room.is_outdoor
        ):
            bed_for_ws = next((fn for fn in room.furniture if fn.furniture_type == "bed"), None)
            if room.normalized_type in (RoomType.MASTER_BEDROOM, RoomType.BEDROOM):
                ws_pts: list[tuple[Point2D, float]] = []
                if bed_for_ws is not None:
                    b_pt, b_ang, _ = _snap_to_room_edge(room, bed_for_ws.centroid_mm)
                    for sign in (-1.0, 1.0):
                        raw_ws = _offset_along_wall(b_pt, b_ang, sign * 750.0)
                        s_pt, s_ang, _ = _snap_to_room_edge(room, raw_ws)
                        ws_pts.append((s_pt, s_ang))
                elif valid_walls:
                    best_w = max(valid_walls, key=lambda c: c.line.length)
                    if best_w.line.length >= 1600.0:
                        for frac in (0.32, 0.68):
                            p = best_w.line.interpolate(frac, normalized=True)
                            ws_pts.append(
                                (
                                    Point2D(x_mm=round(float(p.x), 1), y_mm=round(float(p.y), 1)),
                                    best_w.angle_deg,
                                )
                            )
                for ws_idx, (ws_pt, ws_ang) in enumerate(ws_pts, start=1):
                    ws_id = _next_dev("WS")
                    room_lum_ids.append(ws_id)
                    room_lum_group_b.append(ws_id)
                    devices.append(
                        PlacedDevice(
                            device_id=ws_id,
                            room_id=room.id,
                            device_type=DeviceType.WALL_SCONCE,
                            label=f"Decorative Wall Sconce {ws_idx} ({room.raw_label})",
                            location_mm=ws_pt,
                            mounting_height_mm=1600.0,
                            wall_angle_deg=ws_ang,
                            ip_rating="IP20",
                            switch_group="b",
                            luminaire_wattage_w=8.0,
                            luminaire_fixture_type="3000K Decorative Wall Sconce",
                            lumens=700,
                            target_lux=target_lux,
                        )
                    )
            elif (
                room.normalized_type in (RoomType.LIVING_ROOM, RoomType.KITCHEN_LIVING)
                and valid_walls
            ):
                best_w = max(valid_walls, key=lambda c: c.line.length)
                if best_w.line.length >= 1800.0:
                    for ws_idx, frac in enumerate((0.30, 0.70), start=1):
                        p = best_w.line.interpolate(frac, normalized=True)
                        ws_id = _next_dev("WS")
                        room_lum_ids.append(ws_id)
                        room_lum_group_b.append(ws_id)
                        devices.append(
                            PlacedDevice(
                                device_id=ws_id,
                                room_id=room.id,
                                device_type=DeviceType.WALL_SCONCE,
                                label=f"Decorative Wall Sconce {ws_idx} ({room.raw_label})",
                                location_mm=Point2D(
                                    x_mm=round(float(p.x), 1), y_mm=round(float(p.y), 1)
                                ),
                                mounting_height_mm=1800.0,
                                wall_angle_deg=best_w.angle_deg,
                                ip_rating="IP20",
                                switch_group="b",
                                luminaire_wattage_w=10.0,
                                luminaire_fixture_type="3000K Decorative Wall Sconce",
                                lumens=900,
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

        # 2. Room Light Switch(es) with deterministic 1G1W / 2G1W / 1G2W (2-way αλέ-ρετούρ)
        sw_pt, sw_ang = _place_room_switch(
            room, room_doors, valid_walls, room_wz, indoor_union=indoor_union
        )
        sw_is_indoors = not indoor_union.is_empty and float(
            indoor_union.distance(Point(sw_pt.x_mm, sw_pt.y_mm))
        ) <= 180.0
        sw_ip = (
            "IP20"
            if (room.is_outdoor and (room_doors or sw_is_indoors))
            else ("IP65" if room.is_outdoor else "IP20")
        )

        bed_furn = next((fn for fn in room.furniture if fn.furniture_type == "bed"), None)
        is_master_bed = room.normalized_type == RoomType.MASTER_BEDROOM or (
            room.normalized_type == RoomType.BEDROOM
            and any(tok in room.raw_label.lower() for tok in ("master", "κυριο", "κύριο", "main"))
        )
        # Cyprus MOEC practice: bedrooms with beds (or master bedrooms) get 2-way αλέ-ρετούρ
        is_two_way_bedroom = is_master_bed or (
            room.normalized_type == RoomType.BEDROOM and bed_furn is not None
        )
        rminx, rminy, rmaxx, rmaxy = rpoly.bounds
        room_span_mm = max(rmaxx - rminx, rmaxy - rminy)
        is_two_way_circulation = room.normalized_type in (
            RoomType.CORRIDOR,
            RoomType.STAIRWELL,
        ) and (room_span_mm >= 3500.0 or len(room_doors) >= 2)
        is_two_way_multi_entry = (
            room.normalized_type
            in (RoomType.KITCHEN, RoomType.LIVING_ROOM, RoomType.KITCHEN_LIVING)
            and _has_separated_doors(room_doors, min_sep_mm=1800.0)
        )

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
            # 3-Pole Fan Isolator outside wet room beside switch at H=+2200mm (clear of DB1)
            if fan_id is not None:
                db_loc = building.db_anchor.location_mm
                fi_pos = _offset_along_wall(sw_pt, sw_ang, 320.0)
                fi_neg = _offset_along_wall(sw_pt, sw_ang, -320.0)
                pos_ok = not rpoly.contains(Point(fi_pos.x_mm, fi_pos.y_mm))
                neg_ok = not rpoly.contains(Point(fi_neg.x_mm, fi_neg.y_mm))
                d_pos = math.hypot(fi_pos.x_mm - db_loc.x_mm, fi_pos.y_mm - db_loc.y_mm)
                d_neg = math.hypot(fi_neg.x_mm - db_loc.x_mm, fi_neg.y_mm - db_loc.y_mm)
                if pos_ok and neg_ok:
                    fi_cand = fi_pos if d_pos >= d_neg else fi_neg
                elif pos_ok:
                    fi_cand = fi_pos
                else:
                    fi_cand = fi_neg
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
            # Add secondary 2-way (αλέ-ρετούρ) switch in large/multi-entry open rooms
            need_far_sw = (
                room.area_m2 >= 30.0 or len(room_doors) >= 3 or is_two_way_multi_entry
            )
            if need_far_sw and (len(room_doors) >= 2 or valid_walls):
                if len(room_doors) >= 2:
                    far_door = max(
                        room_doors[1:],
                        key=lambda d: math.hypot(
                            d.hinge.x_mm - sw_pt.x_mm, d.hinge.y_mm - sw_pt.y_mm
                        ),
                    )
                    far_loc, far_ang = _place_room_switch(
                        room, [far_door], valid_walls, room_wz, indoor_union=indoor_union
                    )
                else:
                    interior_cands = [c for c in valid_walls if not c.is_exterior]
                    pool = interior_cands if interior_cands else valid_walls
                    far_cand = max(
                        pool,
                        key=lambda c: Point(sw_pt.x_mm, sw_pt.y_mm).distance(
                            c.line.interpolate(0.5, normalized=True)
                        ),
                    )
                    far_pt = far_cand.line.interpolate(0.5, normalized=True)
                    far_loc = Point2D(
                        x_mm=round(float(far_pt.x), 1), y_mm=round(float(far_pt.y), 1)
                    )
                    far_ang = far_cand.angle_deg
                devices.append(
                    PlacedDevice(
                        device_id=_next_dev("SW"),
                        room_id=room.id,
                        device_type=DeviceType.SWITCH,
                        label=f"1G2W Secondary 2-Way Switch ({room.raw_label})",
                        location_mm=far_loc,
                        mounting_height_mm=1200.0,
                        wall_angle_deg=far_ang,
                        ip_rating=sw_ip,
                        switch_group="a",
                        switch_type="1G2W",
                        controlled_device_ids=room_lum_group_a or room_lum_ids,
                    )
                )
        elif is_two_way_circulation or is_two_way_bedroom or is_two_way_multi_entry:
            # Primary 2-way (αλέ-ρετούρ) switch at room entry door
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
            # Secondary 2-way switch snapped to bed headboard wall, second doorway, or interior wall
            if is_two_way_bedroom and bed_furn is not None:
                # Snap to nearest valid solid wall segment beside the bed (never over a window!)
                bed_pt_sh = Point(bed_furn.centroid_mm.x_mm, bed_furn.centroid_mm.y_mm)
                if valid_walls:
                    best_vw = min(valid_walls, key=lambda c: float(c.line.distance(bed_pt_sh)))
                    proj_pt = best_vw.line.interpolate(best_vw.line.project(bed_pt_sh))
                    sw2_loc = Point2D(
                        x_mm=round(float(proj_pt.x), 1), y_mm=round(float(proj_pt.y), 1)
                    )
                    sw2_ang = best_vw.angle_deg
                else:
                    b_loc, b_ang, _bed_edge = _snap_to_room_edge(room, bed_furn.centroid_mm)
                    sw2_loc = b_loc
                    sw2_ang = b_ang
            elif is_two_way_multi_entry and len(room_doors) >= 2:
                far_door = max(
                    room_doors[1:],
                    key=lambda d: math.hypot(
                        d.hinge.x_mm - sw_pt.x_mm, d.hinge.y_mm - sw_pt.y_mm
                    ),
                )
                sw2_loc, sw2_ang = _place_room_switch(
                    room, [far_door], valid_walls, room_wz, indoor_union=indoor_union
                )
            elif valid_walls:
                if is_two_way_bedroom:
                    # Prefer solid interior headboard wall over distant exterior window walls
                    int_walls = [
                        c for c in valid_walls if not c.is_exterior and c.line.length >= 900.0
                    ]
                    pool = int_walls if int_walls else valid_walls
                    bed_wall = max(pool, key=lambda c: c.line.length)
                    bed_pt = bed_wall.line.interpolate(0.45, normalized=True)
                    sw2_loc = Point2D(
                        x_mm=round(float(bed_pt.x), 1), y_mm=round(float(bed_pt.y), 1)
                    )
                    sw2_ang = bed_wall.angle_deg
                else:
                    far_cand = max(
                        valid_walls,
                        key=lambda c: Point(sw_pt.x_mm, sw_pt.y_mm).distance(
                            c.line.interpolate(0.5, normalized=True)
                        ),
                    )
                    far_pt = far_cand.line.interpolate(0.5, normalized=True)
                    sw2_loc = Point2D(
                        x_mm=round(float(far_pt.x), 1), y_mm=round(float(far_pt.y), 1)
                    )
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
                        if is_two_way_bedroom
                        else f"1G2W Far-End Switch ({room.raw_label})"
                    ),
                    location_mm=sw2_loc,
                    mounting_height_mm=750.0 if is_two_way_bedroom else 1200.0,
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
            bed_furn_s = bed_furn
            furn_sock_pts: list[tuple[Point2D, float]] = []
            if bed_furn_s is not None and room.normalized_type in (
                RoomType.MASTER_BEDROOM,
                RoomType.BEDROOM,
            ):
                bwall, bed_ang, bed_edge = _snap_to_room_edge(room, bed_furn_s.centroid_mm)
                b_proj = float(bed_edge.project(Point(bwall.x_mm, bwall.y_mm)))
                left_pt = bed_edge.interpolate(max(220.0, b_proj - 950.0))
                right_pt = bed_edge.interpolate(min(bed_edge.length - 220.0, b_proj + 950.0))
                if left_pt.distance(right_pt) >= 500.0:
                    l_p2d = Point2D(
                        x_mm=round(float(left_pt.x), 1), y_mm=round(float(left_pt.y), 1)
                    )
                    r_p2d = Point2D(
                        x_mm=round(float(right_pt.x), 1), y_mm=round(float(right_pt.y), 1)
                    )
                    furn_sock_pts.append((l_p2d, bed_ang))
                    furn_sock_pts.append((r_p2d, bed_ang))

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

    # 7. Stage 5 Residential & Commercial Systems (ELV, Security, Smoke/Heat, Shutters, Plant, Site)
    s5_devs, s5_flags = place_stage5_residential_systems(
        building,
        devices,
        active_brief,
        telecom_override=telecom_override,
    )
    devices.extend(s5_devs)
    flags.extend(s5_flags)

    # 8. Deterministic spatial de-collision pass (ceiling, wall, back-to-back & exterior)
    devices = _decollide_placed_devices(
        building,
        devices,
        wet_zones,
        meter_override=meter_override,
        earth_pit_override=earth_pit_override,
        telecom_override=telecom_override,
    )

    return devices, flags


_CEILING_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.LUMINAIRE,
        DeviceType.PENDANT_LIGHT,
        DeviceType.EXHAUST_FAN,
        DeviceType.SMOKE_DETECTOR,
        DeviceType.HEAT_DETECTOR,
        DeviceType.WIFI_AP,
    }
)

_EXTERIOR_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.EAC_METER_CABINET,
        DeviceType.EARTH_PIT,
        DeviceType.EV_WALLBOX,
        DeviceType.HEAT_PUMP_OUTDOOR,
        DeviceType.VRV_OUTDOOR,
        DeviceType.GATE_MOTOR,
        DeviceType.POOL_SUB_DB,
        DeviceType.GARDEN_BOLLARD,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.CCTV_CAMERA,
        DeviceType.ALARM_SIREN,
        DeviceType.INTERCOM_OUTDOOR,
    }
)

_PAIRED_WALL_ACCESSORIES: frozenset[frozenset[DeviceType]] = frozenset(
    {
        frozenset({DeviceType.DATA_RJ45, DeviceType.TV_OUTLET}),
        frozenset({DeviceType.COOKER_OUTLET, DeviceType.COOKER_CONTROL_UNIT}),
        frozenset({DeviceType.WATER_HEATER_ISOLATOR, DeviceType.WATER_HEATER_DP_SWITCH}),
        frozenset({DeviceType.SWITCH, DeviceType.FAN_ISOLATOR_3P}),
    }
)


def _wall_priority(dev: PlacedDevice, *, has_telecom_override: bool) -> int:
    if dev.device_type == DeviceType.TELECOM_CABINET and has_telecom_override:
        return 0
    if dev.device_type in (
        DeviceType.SWITCH,
        DeviceType.DIMMER_SWITCH,
        DeviceType.FAN_ISOLATOR_3P,
        DeviceType.MIRROR_LIGHT,
    ):
        return 0
    if dev.device_type == DeviceType.SOCKET_TWIN:
        return 1
    if dev.device_type in (
        DeviceType.COOKER_OUTLET,
        DeviceType.OVEN_OUTLET,
        DeviceType.WATER_HEATER_ISOLATOR,
        DeviceType.APPLIANCE_OUTLET,
        DeviceType.SPLIT_AC,
        DeviceType.HEAT_PUMP_HYDROBOX,
        DeviceType.UFH_MANIFOLD,
        DeviceType.WATER_BOOSTER_PUMP,
        DeviceType.VRV_INDOOR_ISOLATOR,
    ):
        return 2
    return 3


def _local_wall_angle(room: Room, x_mm: float, y_mm: float) -> float:
    pts = room.polygon_mm
    n = len(pts)
    if n < 2:
        return 0.0
    pt = Point(x_mm, y_mm)
    best_d = float("inf")
    best_ang = 0.0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        seg = LineString([(a.x_mm, a.y_mm), (b.x_mm, b.y_mm)])
        d = float(seg.distance(pt))
        if d < best_d:
            best_d = d
            best_ang = math.degrees(math.atan2(b.y_mm - a.y_mm, b.x_mm - a.x_mm)) % 360.0
    return round(best_ang, 1)


def _decollide_placed_devices(
    building: BuildingModel,
    devices: list[PlacedDevice],
    wet_zones: list[RoomWetZones],
    *,
    meter_override: Point2D | None = None,
    earth_pit_override: Point2D | None = None,
    telecom_override: Point2D | None = None,
) -> list[PlacedDevice]:
    """Deterministically separate colliding ceiling, wall & exterior devices while keeping rules."""
    if not devices or not building.rooms:
        return devices

    rooms_by_id = {r.id: r for r in building.rooms}
    rpoly_by_id = {
        r.id: Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm]) for r in building.rooms
    }
    wz_by_room = {w.room_id: w for w in wet_zones}

    win_parts = [
        LineString([(w.start.x_mm, w.start.y_mm), (w.end.x_mm, w.end.y_mm)]).buffer(120.0)
        for w in building.windows
    ]
    all_window_buf: BaseGeometry = unary_union(win_parts) if win_parts else Polygon()
    door_parts = [_door_forbidden_geometry(d) for d in building.doors]
    all_door_buf: BaseGeometry = unary_union(door_parts) if door_parts else Polygon()

    updated: list[PlacedDevice] = list(devices)

    # Pass A: Ceiling devices (separate exhaust fans, smoke/heat & Wi-Fi APs from luminaires)
    accepted_ceiling: list[tuple[float, float, str]] = []
    # Accept primary luminaires & pendants first
    ceiling_indices = [
        i for i, d in enumerate(updated) if d.device_type in _CEILING_DEVICE_TYPES
    ]
    ceiling_indices.sort(
        key=lambda i: (
            0
            if updated[i].device_type in (DeviceType.LUMINAIRE, DeviceType.PENDANT_LIGHT)
            else 1,
            i,
        )
    )
    for idx in ceiling_indices:
        dev = updated[idx]
        room = rooms_by_id.get(dev.room_id)
        if room is None:
            accepted_ceiling.append((dev.location_mm.x_mm, dev.location_mm.y_mm, ""))
            continue
        flr = room.floor
        x0, y0 = dev.location_mm.x_mm, dev.location_mm.y_mm
        same_flr = [(ax, ay) for ax, ay, af in accepted_ceiling if af == flr]

        if dev.device_type in (DeviceType.LUMINAIRE, DeviceType.PENDANT_LIGHT):
            accepted_ceiling.append((x0, y0, flr))
            continue

        rpoly = rpoly_by_id[room.id]
        cent = room.centroid_mm()
        avoid_centroid = (
            dev.device_type
            in (DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR, DeviceType.WIFI_AP)
            and room.area_m2 >= 6.0
        )

        def _ceil_min_d(cx: float, cy: float, pts: list[tuple[float, float]]) -> float:
            if not pts:
                return 9999.0
            return min(math.hypot(cx - ax, cy - ay) for ax, ay in pts)

        cur_d = _ceil_min_d(x0, y0, same_flr)
        cent_d = math.hypot(x0 - cent.x_mm, y0 - cent.y_mm)
        if cur_d < 360.0 or (avoid_centroid and cent_d < 320.0):
            inner = rpoly.buffer(-160.0)
            target_poly = inner if (not inner.is_empty and inner.area > 0.0) else rpoly
            bminx, bminy, bmaxx, bmaxy = rpoly.bounds
            candidates: list[tuple[float, float]] = [
                (bminx + 0.80 * (bmaxx - bminx), bminy + 0.80 * (bmaxy - bminy)),
                (bminx + 0.22 * (bmaxx - bminx), bminy + 0.78 * (bmaxy - bminy)),
                (bminx + 0.78 * (bmaxx - bminx), bminy + 0.22 * (bmaxy - bminy)),
                (bminx + 0.22 * (bmaxx - bminx), bminy + 0.22 * (bmaxy - bminy)),
            ]
            for rad in (400.0, 560.0, 740.0, 950.0, 1200.0):
                for deg in range(0, 360, 30):
                    ar = math.radians(deg)
                    candidates.append(
                        (cent.x_mm + rad * math.cos(ar), cent.y_mm + rad * math.sin(ar))
                    )

            best_xy = (x0, y0)
            best_score = cur_d + (min(cent_d, 400.0) * 0.25 if avoid_centroid else 0.0)
            for cx, cy in candidates:
                if not target_poly.contains(Point(cx, cy)):
                    continue
                d_peer = _ceil_min_d(cx, cy, same_flr)
                d_cent = math.hypot(cx - cent.x_mm, cy - cent.y_mm)
                score = d_peer + (min(d_cent, 420.0) * 0.25 if avoid_centroid else 0.0)
                if d_peer >= 440.0 and ((not avoid_centroid) or d_cent >= 360.0):
                    best_xy = (cx, cy)
                    break
                if score > best_score:
                    best_score = score
                    best_xy = (cx, cy)

            updated[idx] = dev.model_copy(
                update={
                    "location_mm": Point2D(
                        x_mm=round(best_xy[0], 1), y_mm=round(best_xy[1], 1)
                    )
                }
            )
            x0, y0 = best_xy
        accepted_ceiling.append((x0, y0, flr))

    # Pass B: Wall-mounted devices (stagger same-room & back-to-back partition wall devices)
    wall_indices = [
        i
        for i, d in enumerate(updated)
        if d.device_type not in _CEILING_DEVICE_TYPES
        and d.device_type not in _EXTERIOR_DEVICE_TYPES
    ]
    wall_indices.sort(
        key=lambda i: (
            _wall_priority(updated[i], has_telecom_override=telecom_override is not None),
            i,
        )
    )
    accepted_wall: list[tuple[float, float, str, DeviceType]] = []
    # Seed DB / sub-board wall footprints so switches/keypads/PIRs never sit over MDB/DB1
    db_pt = Point(building.db_anchor.location_mm.x_mm, building.db_anchor.location_mm.y_mm)
    db_flr = building.rooms[0].floor if building.rooms else "GF"
    if building.rooms:
        best_r = min(building.rooms, key=lambda r: rpoly_by_id[r.id].distance(db_pt))
        db_flr = best_r.floor
    accepted_wall.append(
        (
            building.db_anchor.location_mm.x_mm,
            building.db_anchor.location_mm.y_mm,
            db_flr,
            DeviceType.TELECOM_CABINET,
        )
    )
    for sb in building.sub_boards:
        accepted_wall.append(
            (sb.location_mm.x_mm, sb.location_mm.y_mm, sb.floor, DeviceType.TELECOM_CABINET)
        )

    def _pair_min_sep(a: DeviceType, b: DeviceType) -> float:
        pair = frozenset({a, b})
        if pair == frozenset({DeviceType.TELECOM_CABINET, DeviceType.ALARM_PANEL}):
            return 420.0
        if pair in _PAIRED_WALL_ACCESSORIES:
            return 220.0
        return 290.0

    for idx in wall_indices:
        dev = updated[idx]
        room = rooms_by_id.get(dev.room_id)
        if room is None:
            accepted_wall.append((dev.location_mm.x_mm, dev.location_mm.y_mm, "", dev.device_type))
            continue
        flr = room.floor
        x0, y0 = dev.location_mm.x_mm, dev.location_mm.y_mm

        # Never relocate explicitly overridden telecom rack or wet-room exterior switches/isolators
        is_locked = (
            (dev.device_type == DeviceType.TELECOM_CABINET and telecom_override is not None)
            or (
                room.is_wet_room
                and dev.device_type in (DeviceType.SWITCH, DeviceType.FAN_ISOLATOR_3P)
            )
        )
        same_flr_wall = [(ax, ay, at) for ax, ay, af, at in accepted_wall if af == flr]

        def _violates(
            cx: float,
            cy: float,
            dt: DeviceType,
            peers: list[tuple[float, float, DeviceType]],
        ) -> bool:
            return any(
                math.hypot(cx - ax, cy - ay) < _pair_min_sep(dt, at) for ax, ay, at in peers
            )

        def _min_peer_d(
            cx: float,
            cy: float,
            peers: list[tuple[float, float, DeviceType]],
        ) -> float:
            if not peers:
                return 9999.0
            return min(math.hypot(cx - ax, cy - ay) for ax, ay, _ in peers)

        if (not is_locked) and _violates(x0, y0, dev.device_type, same_flr_wall):
            rpoly = rpoly_by_id[room.id]
            boundary = rpoly.exterior
            perim = float(boundary.length)
            room_wz = wz_by_room.get(room.id)
            if perim > 400.0:
                s0 = float(boundary.project(Point(x0, y0)))
                offsets = (
                    340.0,
                    -340.0,
                    520.0,
                    -520.0,
                    740.0,
                    -740.0,
                    980.0,
                    -980.0,
                    1260.0,
                    -1260.0,
                    1580.0,
                    -1580.0,
                    1950.0,
                    -1950.0,
                    2400.0,
                    -2400.0,
                    2900.0,
                    -2900.0,
                    3500.0,
                    -3500.0,
                    4200.0,
                    -4200.0,
                    5000.0,
                    -5000.0,
                )
                best_xy = (x0, y0)
                best_d = _min_peer_d(x0, y0, same_flr_wall)
                found_clear = False
                for delta in offsets:
                    pt = boundary.interpolate((s0 + delta) % perim)
                    cx, cy = float(pt.x), float(pt.y)
                    cand_p2d = Point2D(x_mm=round(cx, 1), y_mm=round(cy, 1))
                    if dev.device_type == DeviceType.SOCKET_TWIN:
                        if (not all_window_buf.is_empty) and all_window_buf.contains(pt):
                            continue
                        if (not all_door_buf.is_empty) and all_door_buf.contains(pt):
                            continue
                        if is_point_in_socket_exclusion(cand_p2d, room_wz):
                            continue
                    elif (
                        dev.device_type != DeviceType.SHUTTER_SWITCH
                        and (not all_window_buf.is_empty)
                        and all_window_buf.contains(pt)
                    ):
                        continue
                    if not _violates(cx, cy, dev.device_type, same_flr_wall):
                        best_xy = (cx, cy)
                        found_clear = True
                        break
                    md = _min_peer_d(cx, cy, same_flr_wall)
                    if md > best_d:
                        best_d = md
                        best_xy = (cx, cy)

                if found_clear or best_d > _min_peer_d(x0, y0, same_flr_wall) + 40.0:
                    new_ang = _local_wall_angle(room, best_xy[0], best_xy[1])
                    updated[idx] = dev.model_copy(
                        update={
                            "location_mm": Point2D(
                                x_mm=round(best_xy[0], 1), y_mm=round(best_xy[1], 1)
                            ),
                            "wall_angle_deg": new_ang,
                        }
                    )
                    x0, y0 = best_xy

        accepted_wall.append((x0, y0, flr, dev.device_type))

    # Pass C: Exterior site devices (prevent overlapping exterior markers)
    ext_indices = [i for i, d in enumerate(updated) if d.device_type in _EXTERIOR_DEVICE_TYPES]
    accepted_ext: list[tuple[float, float]] = []
    for idx in ext_indices:
        dev = updated[idx]
        x0, y0 = dev.location_mm.x_mm, dev.location_mm.y_mm
        locked_ext = (
            (dev.device_type == DeviceType.EAC_METER_CABINET and meter_override is not None)
            or (dev.device_type == DeviceType.EARTH_PIT and earth_pit_override is not None)
        )
        ext_close = any(math.hypot(x0 - ax, y0 - ay) < 340.0 for ax, ay in accepted_ext)
        if (not locked_ext) and ext_close:
            for dx, dy in (
                (460.0, 0.0),
                (-460.0, 0.0),
                (0.0, -460.0),
                (0.0, 460.0),
                (680.0, -320.0),
                (-680.0, -320.0),
            ):
                cx, cy = x0 + dx, y0 + dy
                if all(math.hypot(cx - ax, cy - ay) >= 340.0 for ax, ay in accepted_ext):
                    updated[idx] = dev.model_copy(
                        update={"location_mm": Point2D(x_mm=round(cx, 1), y_mm=round(cy, 1))}
                    )
                    x0, y0 = cx, cy
                    break
        accepted_ext.append((x0, y0))

    return updated


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
