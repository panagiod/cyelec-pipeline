"""Stage 5 deterministic placement: Cyprus plant, ELV, security, smoke, KNX & site works."""

from __future__ import annotations

import math

from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union

from cyelec.models.building import BuildingModel, BuildingType, Point2D, Room, RoomType
from cyelec.models.electrical import ResidentialSystemsBrief, ReviewFlag, ReviewSeverity
from cyelec.models.spatial import DeviceType, PlacedDevice
from cyelec.solver.grouper import is_commercial_building

_HABITABLE_SHUTTER_TYPES: frozenset[RoomType] = frozenset(
    {
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN_LIVING,
        RoomType.MASTER_BEDROOM,
        RoomType.BEDROOM,
        RoomType.KITCHEN,
    }
)

_DATA_TV_TYPES: frozenset[RoomType] = frozenset(
    {
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN_LIVING,
        RoomType.MASTER_BEDROOM,
        RoomType.BEDROOM,
        RoomType.OPEN_OFFICE,
        RoomType.MEETING_ROOM,
        RoomType.RETAIL_FLOOR,
        RoomType.SERVER_ROOM,
    }
)

_THERMOSTAT_TYPES: frozenset[RoomType] = frozenset(
    {
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN_LIVING,
        RoomType.MASTER_BEDROOM,
        RoomType.BEDROOM,
        RoomType.OPEN_OFFICE,
        RoomType.MEETING_ROOM,
    }
)


def _wall_point_and_angle(room: Room, frac: float = 0.35) -> tuple[Point2D, float]:
    """Return a clean interior perimeter point and wall tangent angle along a room polygon."""
    pts = room.polygon_mm
    n = len(pts)
    if n < 2:
        return room.centroid_mm(), 0.0
    # Pick longest edge for stable placement
    best_i = max(
        range(n),
        key=lambda i: math.hypot(
            pts[(i + 1) % n].x_mm - pts[i].x_mm,
            pts[(i + 1) % n].y_mm - pts[i].y_mm,
        ),
    )
    p0, p1 = pts[best_i], pts[(best_i + 1) % n]
    x = p0.x_mm + (p1.x_mm - p0.x_mm) * frac
    y = p0.y_mm + (p1.y_mm - p0.y_mm) * frac
    ang = math.degrees(math.atan2(p1.y_mm - p0.y_mm, p1.x_mm - p0.x_mm)) % 360.0
    return Point2D(x_mm=round(x, 1), y_mm=round(y, 1)), round(ang, 1)


def _snap_to_nearest_wall(room: Room, target: Point2D) -> tuple[Point2D, float]:
    """Project a furniture centroid onto the nearest room wall edge."""
    rpoly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
    proj = nearest_points(rpoly.exterior, Point(target.x_mm, target.y_mm))[0]
    pts = room.polygon_mm
    n = len(pts)
    best_ang = 0.0
    best_d = float("inf")
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        mid_x, mid_y = (p0.x_mm + p1.x_mm) * 0.5, (p0.y_mm + p1.y_mm) * 0.5
        d = math.hypot(mid_x - float(proj.x), mid_y - float(proj.y))
        if d < best_d:
            best_d = d
            best_ang = math.degrees(math.atan2(p1.y_mm - p0.y_mm, p1.x_mm - p0.x_mm)) % 360.0
    return Point2D(x_mm=round(float(proj.x), 1), y_mm=round(float(proj.y), 1)), round(best_ang, 1)


def _interior_offset_point(room: Room, dx_mm: float, dy_mm: float) -> Point2D:
    """Return a quadrant-offset ceiling point clear of centered room text."""
    rpoly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
    c = room.centroid_mm()
    minx, miny, maxx, maxy = rpoly.bounds
    sx = max(abs(dx_mm), min((maxx - minx) * 0.25, 2200.0))
    sy = max(abs(dy_mm), min((maxy - miny) * 0.26, 1600.0))
    eff_dx = math.copysign(sx, dx_mm) if dx_mm != 0.0 else 0.0
    eff_dy = math.copysign(sy, dy_mm) if dy_mm != 0.0 else 0.0
    inner = rpoly.buffer(-180.0)
    target_poly = inner if (not inner.is_empty and inner.area > 0.0) else rpoly
    for scale in (1.0, 0.78, 0.56, 0.36):
        cand = Point(c.x_mm + eff_dx * scale, c.y_mm + eff_dy * scale)
        if target_poly.contains(cand):
            return Point2D(x_mm=round(float(cand.x), 1), y_mm=round(float(cand.y), 1))
    rp = rpoly.representative_point()
    return Point2D(x_mm=round(float(rp.x), 1), y_mm=round(float(rp.y), 1))


def _primary_floor_envelope(
    building: BuildingModel,
) -> tuple[Polygon, tuple[float, float, float, float], Room]:
    """Return the primary (GF) floor union polygon, its bounding box, and reference room."""
    if building.sub_boards:
        pfloor = next(
            (sb.floor for sb in building.sub_boards if sb.floor == "GF"),
            building.sub_boards[0].floor,
        )
        prooms = [r for r in building.rooms if r.floor == pfloor] or building.rooms
    else:
        prooms = building.rooms
    polys = [Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm]) for r in prooms]
    union_geom = unary_union(polys)
    bounds = union_geom.bounds
    return union_geom, bounds, prooms[0]


def place_stage5_residential_systems(
    building: BuildingModel,
    existing_devices: list[PlacedDevice],
    brief: ResidentialSystemsBrief,
    *,
    telecom_override: Point2D | None = None,
) -> tuple[list[PlacedDevice], list[ReviewFlag]]:
    """Place Stage 5 Cyprus plant, ELV/security, smoke/heat, KNX & external site devices."""
    if not building.rooms:
        return [], []

    is_comm = is_commercial_building(building)
    is_house = building.building_type == BuildingType.INDEPENDENT_HOUSE
    new_devices: list[PlacedDevice] = []
    flags: list[ReviewFlag] = []
    seq: dict[str, int] = {}

    def _next_id(prefix: str) -> str:
        seq[prefix] = seq.get(prefix, 0) + 1
        return f"DEV-{prefix}{seq[prefix]}"

    primary_union, (minx, miny, maxx, maxy), primary_room = _primary_floor_envelope(building)
    dry_indoor = [r for r in building.rooms if not r.is_wet_room and not r.is_outdoor]
    preferred_tech_types = (
        (RoomType.SERVER_ROOM, RoomType.PLANT_ROOM, RoomType.UTILITY, RoomType.CORRIDOR)
        if is_comm
        else (RoomType.UTILITY, RoomType.PLANT_ROOM, RoomType.CORRIDOR, RoomType.STORAGE)
    )
    utility_room = next(
        (r for t in preferred_tech_types for r in dry_indoor if r.normalized_type == t),
        dry_indoor[0] if dry_indoor else primary_room,
    )
    distinct_floors = list(dict.fromkeys(r.floor for r in building.rooms))

    if not is_comm:
        # 1. Outdoor IP65 PIR Security/Entry Floodlight on primary front facade
        pir_ext_loc = Point2D(x_mm=round((minx + maxx) * 0.5, 1), y_mm=round(miny - 140.0, 1))
        new_devices.append(
            PlacedDevice(
                device_id=_next_id("OPIR"),
                room_id=primary_room.id,
                device_type=DeviceType.OUTDOOR_PIR_LIGHT,
                label="IP65 Outdoor PIR Entry Floodlight (H+2400)",
                location_mm=pir_ext_loc,
                mounting_height_mm=2400.0,
                ip_rating="IP65",
                luminaire_wattage_w=20.0,
                luminaire_fixture_type="IP65 LED PIR Floodlight",
                lumens=2100,
                target_lux=80,
            )
        )

        # 2. Motorized Window Shutters (beside room windows or exterior wall)
        if brief.include_electric_shutters:
            win_by_room: dict[str, list[Point2D]] = {}
            for w in building.windows:
                mid = Point2D(
                    x_mm=round((w.start.x_mm + w.end.x_mm) * 0.5, 1),
                    y_mm=round((w.start.y_mm + w.end.y_mm) * 0.5, 1),
                )
                win_by_room.setdefault(w.room_id, []).append(mid)
            for room in building.rooms:
                if room.normalized_type not in _HABITABLE_SHUTTER_TYPES:
                    continue
                w_pts = win_by_room.get(room.id)
                if w_pts:
                    sh_pt, sh_ang = _snap_to_nearest_wall(room, w_pts[0])
                else:
                    sh_pt, sh_ang = _wall_point_and_angle(room, frac=0.72)
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("SH"),
                        room_id=room.id,
                        device_type=DeviceType.SHUTTER_SWITCH,
                        label=f"Motorized Shutter Switch ({room.raw_label})",
                        location_mm=sh_pt,
                        mounting_height_mm=1200.0,
                        wall_angle_deg=sh_ang,
                        ip_rating="IP20",
                    )
                )

        # 3. Kitchen FCU Spurs (Extractor Hood +2100mm, Fridge-Freezer +450mm, Microwave +1150mm)
        if brief.include_kitchen_fcus:
            first_kitchen = next(
                (
                    r
                    for r in building.rooms
                    if r.normalized_type in (RoomType.KITCHEN, RoomType.KITCHEN_LIVING)
                ),
                None,
            )
            if first_kitchen is not None:
                fridge_furn = next(
                    (fn for fn in first_kitchen.furniture if fn.furniture_type == "fridge"), None
                )
                stove_furn = next(
                    (fn for fn in first_kitchen.furniture if fn.furniture_type == "stove"), None
                )
                counter_furn = next(
                    (
                        fn
                        for fn in first_kitchen.furniture
                        if fn.furniture_type == "kitchen_counter"
                    ),
                    None,
                )
                fcu_specs = [
                    (
                        "Extractor Hood 13A FCU",
                        2100.0,
                        stove_furn.centroid_mm
                        if stove_furn
                        else (counter_furn.centroid_mm if counter_furn else None),
                        0.22,
                    ),
                    (
                        "Fridge-Freezer 13A FCU",
                        450.0,
                        fridge_furn.centroid_mm if fridge_furn else None,
                        0.48,
                    ),
                    (
                        "Built-in Microwave 13A FCU",
                        1150.0,
                        counter_furn.centroid_mm if counter_furn else None,
                        0.76,
                    ),
                ]
                for fcu_lbl, fcu_h, snap_pt, frac in fcu_specs:
                    loc, ang = (
                        _snap_to_nearest_wall(first_kitchen, snap_pt)
                        if snap_pt is not None
                        else _wall_point_and_angle(first_kitchen, frac=frac)
                    )
                    new_devices.append(
                        PlacedDevice(
                            device_id=_next_id("FCU"),
                            room_id=first_kitchen.id,
                            device_type=DeviceType.KITCHEN_FCU,
                            label=f"{fcu_lbl} ({first_kitchen.raw_label})",
                            location_mm=loc,
                            mounting_height_mm=fcu_h,
                            wall_angle_deg=ang,
                            ip_rating="IP20",
                        )
                    )

        # 4. Central HVAC Plant (Air-to-Water Heat Pump + UFH OR VRV/VRF)
        if brief.hvac_mode == "heat_pump_ufh":
            hp_out_loc = Point2D(x_mm=round(maxx + 550.0, 1), y_mm=round(miny + 1200.0, 1))
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("HP"),
                    room_id=utility_room.id,
                    device_type=DeviceType.HEAT_PUMP_OUTDOOR,
                    label=f"3P Air-to-Water Heat Pump Outdoor ({brief.heat_pump_kw:g} kWe)",
                    location_mm=hp_out_loc,
                    mounting_height_mm=400.0,
                    ip_rating="IP65",
                )
            )
            hb_loc, hb_ang = _wall_point_and_angle(utility_room, frac=0.30)
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("HB"),
                    room_id=utility_room.id,
                    device_type=DeviceType.HEAT_PUMP_HYDROBOX,
                    label=f"Heat Pump Hydrobox & Buffer ({utility_room.raw_label})",
                    location_mm=hb_loc,
                    mounting_height_mm=1400.0,
                    wall_angle_deg=hb_ang,
                    ip_rating="IP20",
                )
            )
            # 1 UFH manifold per floor
            for flr in distinct_floors:
                flr_host = next(
                    (
                        r
                        for r in building.rooms
                        if r.floor == flr
                        and r.normalized_type
                        in (RoomType.CORRIDOR, RoomType.UTILITY, RoomType.STORAGE)
                    ),
                    next(r for r in building.rooms if r.floor == flr),
                )
                mf_loc, mf_ang = _wall_point_and_angle(flr_host, frac=0.62)
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("UFH"),
                        room_id=flr_host.id,
                        device_type=DeviceType.UFH_MANIFOLD,
                        label=f"UFH Manifold Wiring Centre ({flr})",
                        location_mm=mf_loc,
                        mounting_height_mm=600.0,
                        wall_angle_deg=mf_ang,
                        ip_rating="IP20",
                    )
                )
            for room in building.rooms:
                if room.normalized_type in _THERMOSTAT_TYPES:
                    th_loc, th_ang = _wall_point_and_angle(room, frac=0.18)
                    new_devices.append(
                        PlacedDevice(
                            device_id=_next_id("TH"),
                            room_id=room.id,
                            device_type=DeviceType.ROOM_THERMOSTAT,
                            label=f"UFH Room Thermostat ({room.raw_label})",
                            location_mm=th_loc,
                            mounting_height_mm=1500.0,
                            wall_angle_deg=th_ang,
                            ip_rating="IP20",
                        )
                    )
        elif brief.hvac_mode == "vrv":
            vrv_out_loc = Point2D(x_mm=round(maxx + 550.0, 1), y_mm=round(maxy - 1200.0, 1))
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("VRV"),
                    room_id=utility_room.id,
                    device_type=DeviceType.VRV_OUTDOOR,
                    label=f"3P VRV/VRF Outdoor Unit ({brief.vrv_outdoor_kw:g} kWe)",
                    location_mm=vrv_out_loc,
                    mounting_height_mm=400.0,
                    ip_rating="IP65",
                )
            )
            for room in building.rooms:
                if room.normalized_type in _THERMOSTAT_TYPES:
                    iso_loc, iso_ang = _wall_point_and_angle(room, frac=0.82)
                    new_devices.append(
                        PlacedDevice(
                            device_id=_next_id("FCUI"),
                            room_id=room.id,
                            device_type=DeviceType.VRV_INDOOR_ISOLATOR,
                            label=f"VRV Indoor FCU Isolator ({room.raw_label})",
                            location_mm=iso_loc,
                            mounting_height_mm=2400.0,
                            wall_angle_deg=iso_ang,
                            ip_rating="IP20",
                        )
                    )
                    th_loc, th_ang = _wall_point_and_angle(room, frac=0.18)
                    new_devices.append(
                        PlacedDevice(
                            device_id=_next_id("TH"),
                            room_id=room.id,
                            device_type=DeviceType.ROOM_THERMOSTAT,
                            label=f"VRV Wall Controller ({room.raw_label})",
                            location_mm=th_loc,
                            mounting_height_mm=1500.0,
                            wall_angle_deg=th_ang,
                            ip_rating="IP20",
                        )
                    )

        # 5. Water Pressure Booster Pump, Sliding Gate Motor, EV Wallbox & Optional Pool Sub-DB
        if brief.include_water_pumps:
            wp_loc, wp_ang = _wall_point_and_angle(utility_room, frac=0.80)
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("WP"),
                    room_id=utility_room.id,
                    device_type=DeviceType.WATER_BOOSTER_PUMP,
                    label="Water Pressure Booster & Hot-Water Recirc. Isolator",
                    location_mm=wp_loc,
                    mounting_height_mm=1400.0,
                    wall_angle_deg=wp_ang,
                    ip_rating="IP55",
                )
            )

        if brief.include_gate_motor and is_house:
            gate_loc = Point2D(x_mm=round(minx + 1200.0, 1), y_mm=round(miny - 1100.0, 1))
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("GATE"),
                    room_id=primary_room.id,
                    device_type=DeviceType.GATE_MOTOR,
                    label="Sliding Yard Gate / Garage Motor + Photocells (IP65)",
                    location_mm=gate_loc,
                    mounting_height_mm=500.0,
                    ip_rating="IP65",
                )
            )

        effective_ev_kw = (
            brief.ev_charger_kw if brief.ev_charger_kw is not None else building.ev_charger_kw
        )
        if effective_ev_kw > 0.0:
            ev_loc = Point2D(x_mm=round(minx - 240.0, 1), y_mm=round(miny + 1500.0, 1))
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("EV"),
                    room_id=primary_room.id,
                    device_type=DeviceType.EV_WALLBOX,
                    label=f"EV Mode 3 Wallbox {effective_ev_kw:g}kW (BS 7671 Sec. 722)",
                    location_mm=ev_loc,
                    mounting_height_mm=1300.0,
                    ip_rating="IP65",
                )
            )

        if brief.include_pool:
            pool_loc = Point2D(x_mm=round(maxx + 1200.0, 1), y_mm=round(maxy - 800.0, 1))
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("POOL"),
                    room_id=primary_room.id,
                    device_type=DeviceType.POOL_SUB_DB,
                    label="Pool Sub-DB & SELV 12V Transformer (BS 7671 Sec. 702)",
                    location_mm=pool_loc,
                    mounting_height_mm=1500.0,
                    ip_rating="IP65",
                )
            )
            flags.append(
                ReviewFlag(
                    code="BS7671_SECTION_702_SWIMMING_POOL",
                    severity=ReviewSeverity.INFO,
                    category="bs7671",
                    regulation_ref="BS 7671 Section 702",
                    message=(
                        "Swimming pool feeder & IP65 Pool Sub-DB included; verify Zone 0/1/2 "
                        "clearances, SELV <=12V AC underwater lighting, and supplementary "
                        "equipotential bonding on final landscape plan."
                    ),
                    requires_etek_signoff=True,
                )
            )

    # 6. ELV / Weak-Current & Security Systems (E-103) — Residential & Commercial
    if brief.include_elv:
        if telecom_override is not None:
            rack_loc = Point2D(
                x_mm=round(telecom_override.x_mm, 1), y_mm=round(telecom_override.y_mm, 1)
            )
            rack_ang = 0.0
        else:
            rack_loc, rack_ang = _wall_point_and_angle(utility_room, frac=0.42)

        new_devices.append(
            PlacedDevice(
                device_id=_next_id("RACK"),
                room_id=utility_room.id,
                device_type=DeviceType.TELECOM_CABINET,
                label=(
                    "12U/18U Structured Cabling Rack (Cat6A Patch / Fiber ONT / PoE Switch)"
                    if is_comm
                    else "9U Telecom Cabinet (Cat6 Patch / Cyta Fiber ONT / PoE Switch)"
                ),
                location_mm=rack_loc,
                mounting_height_mm=1800.0,
                wall_angle_deg=rack_ang,
                ip_rating="IP20",
            )
        )

        # Dual Cat6 RJ45 Data (+ TV/SAT on residential or rooms with detected TV block)
        for room in building.rooms:
            tv_furn = next((fn for fn in room.furniture if fn.furniture_type == "tv"), None)
            if room.normalized_type not in _DATA_TV_TYPES and tv_furn is None:
                continue
            if tv_furn is not None:
                d_loc, d_ang = _snap_to_nearest_wall(room, tv_furn.centroid_mm)
            else:
                d_loc, d_ang = _wall_point_and_angle(room, frac=0.55)
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("DATA"),
                    room_id=room.id,
                    device_type=DeviceType.DATA_RJ45,
                    label=f"2x Cat6 RJ45 Data Outlet ({room.raw_label})",
                    location_mm=d_loc,
                    mounting_height_mm=450.0,
                    wall_angle_deg=d_ang,
                    ip_rating="IP20",
                )
            )
            if (not is_comm) or tv_furn is not None:
                rad = math.radians(d_ang)
                tv_loc = Point2D(
                    x_mm=round(d_loc.x_mm + 260.0 * math.cos(rad), 1),
                    y_mm=round(d_loc.y_mm + 260.0 * math.sin(rad), 1),
                )
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("TV"),
                        room_id=room.id,
                        device_type=DeviceType.TV_OUTLET,
                        label=f"TV / SAT Coax Outlet ({room.raw_label})",
                        location_mm=tv_loc,
                        mounting_height_mm=450.0,
                        wall_angle_deg=d_ang,
                        ip_rating="IP20",
                    )
                )

        # Ceiling PoE Wi-Fi 6 Access Point (1 central AP per floor, offset clear of room centroid)
        for flr in distinct_floors:
            flr_rooms = [r for r in building.rooms if r.floor == flr]
            ap_room = next(
                (
                    r
                    for r in flr_rooms
                    if r.normalized_type
                    in (
                        RoomType.OPEN_OFFICE,
                        RoomType.RETAIL_FLOOR,
                        RoomType.CORRIDOR,
                        RoomType.LIVING_ROOM,
                        RoomType.KITCHEN_LIVING,
                    )
                ),
                flr_rooms[0],
            )
            ap_loc = _interior_offset_point(ap_room, 460.0, -380.0)
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("WIFI"),
                    room_id=ap_room.id,
                    device_type=DeviceType.WIFI_AP,
                    label=f"Ceiling PoE Wi-Fi 6 Access Point ({flr})",
                    location_mm=ap_loc,
                    mounting_height_mm=ap_room.ceiling_height_mm,
                    ip_rating="IP20",
                )
            )

        # Video Intercom / Access Control (1 outdoor entry station + 1 indoor monitor per floor)
        ic_out_loc = Point2D(
            x_mm=round((minx + maxx) * 0.5 - 650.0, 1), y_mm=round(miny - 200.0, 1)
        )
        new_devices.append(
            PlacedDevice(
                device_id=_next_id("ICOUT"),
                room_id=primary_room.id,
                device_type=DeviceType.INTERCOM_OUTDOOR,
                label=(
                    "IP65 Access Control / Video Intercom Entry Reader"
                    if is_comm
                    else "IP65 Video Intercom Outdoor Station + Electric Strike"
                ),
                location_mm=ic_out_loc,
                mounting_height_mm=1500.0,
                ip_rating="IP65",
            )
        )
        for flr in distinct_floors:
            flr_rooms = [r for r in building.rooms if r.floor == flr]
            ic_room = next(
                (
                    r
                    for r in flr_rooms
                    if r.normalized_type
                    in (
                        RoomType.CORRIDOR,
                        RoomType.OPEN_OFFICE,
                        RoomType.RETAIL_FLOOR,
                        RoomType.LIVING_ROOM,
                        RoomType.KITCHEN_LIVING,
                    )
                ),
                flr_rooms[0],
            )
            ic_loc, ic_ang = _wall_point_and_angle(ic_room, frac=0.24)
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("ICIN"),
                    room_id=ic_room.id,
                    device_type=DeviceType.INTERCOM_INDOOR,
                    label=(
                        f"Access / Intercom Reception Console ({flr})"
                        if is_comm
                        else f"Video Intercom Indoor Monitor ({flr})"
                    ),
                    location_mm=ic_loc,
                    mounting_height_mm=1500.0,
                    wall_angle_deg=ic_ang,
                    ip_rating="IP20",
                )
            )

        # Intruder Alarm (offset 680 mm along wall tangent from rack so 9U-IT & ALM stay clear)
        r_rad = math.radians(rack_ang)
        alm_cand = Point2D(
            x_mm=round(rack_loc.x_mm + 680.0 * math.cos(r_rad), 1),
            y_mm=round(rack_loc.y_mm + 680.0 * math.sin(r_rad), 1),
        )
        alm_loc, alm_ang = _snap_to_nearest_wall(utility_room, alm_cand)
        if math.hypot(alm_loc.x_mm - rack_loc.x_mm, alm_loc.y_mm - rack_loc.y_mm) < 420.0:
            alm_loc, alm_ang = _wall_point_and_angle(utility_room, frac=0.78)
        new_devices.append(
            PlacedDevice(
                device_id=_next_id("ALMP"),
                room_id=utility_room.id,
                device_type=DeviceType.ALARM_PANEL,
                label="Intruder Alarm Control Panel (EN 50131 Grade 2)",
                location_mm=alm_loc,
                mounting_height_mm=1600.0,
                wall_angle_deg=alm_ang,
                ip_rating="IP20",
            )
        )
        kp_loc, kp_ang = _wall_point_and_angle(primary_room, frac=0.14)
        new_devices.append(
            PlacedDevice(
                device_id=_next_id("KP"),
                room_id=primary_room.id,
                device_type=DeviceType.ALARM_KEYPAD,
                label="Intruder Alarm Entry Keypad (H+1400)",
                location_mm=kp_loc,
                mounting_height_mm=1400.0,
                wall_angle_deg=kp_ang,
                ip_rating="IP20",
            )
        )
        for room in building.rooms:
            if room.normalized_type in (
                RoomType.LIVING_ROOM,
                RoomType.KITCHEN_LIVING,
                RoomType.CORRIDOR,
                RoomType.MASTER_BEDROOM,
                RoomType.OPEN_OFFICE,
                RoomType.MEETING_ROOM,
                RoomType.RETAIL_FLOOR,
                RoomType.SERVER_ROOM,
            ):
                p_pt = room.polygon_mm[0]
                c = room.centroid_mm()
                # Tuck PIR slightly inside corner toward centroid
                pir_loc = Point2D(
                    x_mm=round(p_pt.x_mm + 0.15 * (c.x_mm - p_pt.x_mm), 1),
                    y_mm=round(p_pt.y_mm + 0.15 * (c.y_mm - p_pt.y_mm), 1),
                )
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("PIR"),
                        room_id=room.id,
                        device_type=DeviceType.ALARM_PIR,
                        label=f"Intruder Alarm Dual-Tech PIR ({room.raw_label})",
                        location_mm=pir_loc,
                        mounting_height_mm=2200.0,
                        ip_rating="IP20",
                    )
                )
        siren_loc = Point2D(x_mm=round(maxx - 800.0, 1), y_mm=round(miny - 200.0, 1))
        new_devices.append(
            PlacedDevice(
                device_id=_next_id("SIR"),
                room_id=primary_room.id,
                device_type=DeviceType.ALARM_SIREN,
                label="External Piezo Alarm Siren + Strobe (IP65 H+3000)",
                location_mm=siren_loc,
                mounting_height_mm=3000.0,
                ip_rating="IP65",
            )
        )

        # Exterior IP67 PoE CCTV Cameras (4 corners on houses & commercial, 2 on apartments)
        cam_corners = (
            [
                (minx - 180.0, miny - 180.0, 225.0, "SW Corner"),
                (maxx + 180.0, miny - 180.0, 315.0, "SE Corner"),
                (maxx + 180.0, maxy + 180.0, 45.0, "NE Corner"),
                (minx - 180.0, maxy + 180.0, 135.0, "NW Corner"),
            ]
            if (is_house or is_comm)
            else [
                (minx - 180.0, miny - 180.0, 225.0, "Front Entry"),
                (maxx + 180.0, maxy + 180.0, 45.0, "Rear Balcony"),
            ]
        )
        for cx, cy, c_ang, corner_lbl in cam_corners:
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("CAM"),
                    room_id=primary_room.id,
                    device_type=DeviceType.CCTV_CAMERA,
                    label=f"IP67 4MP PoE CCTV Camera ({corner_lbl}, H+2800)",
                    location_mm=Point2D(x_mm=round(cx, 1), y_mm=round(cy, 1)),
                    mounting_height_mm=2800.0,
                    wall_angle_deg=c_ang,
                    ip_rating="IP67",
                )
            )

    # 7. BS 5839 Smoke & Kitchen Heat Detectors (Residential Grade D1 / Commercial Fire Detection)
    if brief.include_smoke_detectors:
        for room in building.rooms:
            if room.is_wet_room or room.is_outdoor or room.normalized_type == RoomType.STORAGE:
                continue
            det_loc = _interior_offset_point(room, -460.0, 380.0)
            if room.normalized_type in (RoomType.KITCHEN, RoomType.KITCHEN_LIVING):
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("HD"),
                        room_id=room.id,
                        device_type=DeviceType.HEAT_DETECTOR,
                        label=(
                            f"Fire Alarm Rate-of-Rise Heat Detector ({room.raw_label})"
                            if is_comm
                            else f"Interlinked Heat Detector Grade D1 ({room.raw_label})"
                        ),
                        location_mm=det_loc,
                        mounting_height_mm=room.ceiling_height_mm,
                        ip_rating="IP20",
                    )
                )
            else:
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("SD"),
                        room_id=room.id,
                        device_type=DeviceType.SMOKE_DETECTOR,
                        label=(
                            f"Fire Alarm Optical Smoke Detector ({room.raw_label})"
                            if is_comm
                            else f"Interlinked Optical Smoke Detector Grade D1 ({room.raw_label})"
                        ),
                        location_mm=det_loc,
                        mounting_height_mm=room.ceiling_height_mm,
                        ip_rating="IP20",
                    )
                )

    # 8. Optional Smart Home KNX Scene Keypads (when brief.include_knx=True)
    if (not is_comm) and brief.include_knx:
        for room in building.rooms:
            if room.normalized_type in _HABITABLE_SHUTTER_TYPES:
                k_loc, k_ang = _wall_point_and_angle(room, frac=0.28)
                new_devices.append(
                    PlacedDevice(
                        device_id=_next_id("KNX"),
                        room_id=room.id,
                        device_type=DeviceType.KNX_KEYPAD,
                        label=f"KNX 4-Fold Scene/Dim/Blind Keypad ({room.raw_label})",
                        location_mm=k_loc,
                        mounting_height_mm=1200.0,
                        wall_angle_deg=k_ang,
                        ip_rating="IP20",
                    )
                )

    # 9. External Garden / Perimeter IP65 Bollards (houses with site works)
    if (not is_comm) and brief.include_site_works and is_house:
        bollard_pts = [
            (minx - 900.0, miny - 900.0, "Front-West"),
            ((minx + maxx) * 0.5, miny - 1200.0, "Driveway"),
            (maxx + 900.0, miny - 900.0, "Front-East"),
            ((minx + maxx) * 0.5, maxy + 900.0, "Rear Garden"),
        ]
        for bx, by, blbl in bollard_pts:
            new_devices.append(
                PlacedDevice(
                    device_id=_next_id("GB"),
                    room_id=primary_room.id,
                    device_type=DeviceType.GARDEN_BOLLARD,
                    label=f"IP65 LED Garden Bollard ({blbl})",
                    location_mm=Point2D(x_mm=round(bx, 1), y_mm=round(by, 1)),
                    mounting_height_mm=800.0,
                    ip_rating="IP65",
                    luminaire_wattage_w=12.0,
                    luminaire_fixture_type="IP65 LED Bollard",
                    lumens=1100,
                )
            )

    _ = primary_union  # kept for future boundary-clipped refinement
    _ = existing_devices
    return new_devices, flags
