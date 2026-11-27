"""Deterministic final circuit grouper per BS 7671 Reg 314.1, Appendix 15 & Cyprus EAC practice."""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.building import BuildingModel, BuildingType, Point2D, Room, RoomType
from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitSpec,
    CircuitTopology,
    DesignOverrides,
    ResidentialSystemsBrief,
    ReviewFlag,
    ReviewSeverity,
)
from cyelec.standards_loader import StandardsBundle

_COMMERCIAL_BUILDING_TYPES = frozenset(
    {
        BuildingType.OFFICE,
        BuildingType.SHOP,
        BuildingType.WORKSHOP,
        BuildingType.INDUSTRIAL,
    }
)
_COMMERCIAL_ROOM_TYPES = frozenset(
    {
        RoomType.OPEN_OFFICE,
        RoomType.MEETING_ROOM,
        RoomType.SERVER_ROOM,
        RoomType.RETAIL_FLOOR,
        RoomType.WAREHOUSE,
        RoomType.PLANT_ROOM,
        RoomType.STAIRWELL,
        RoomType.LIFT_SHAFT,
    }
)


def is_commercial_building(building: BuildingModel) -> bool:
    """Return True when building type or room types indicate commercial/industrial MEP scope."""
    if building.building_type in _COMMERCIAL_BUILDING_TYPES:
        return True
    return any(r.normalized_type in _COMMERCIAL_ROOM_TYPES for r in building.rooms)


def _manhattan_m(a: Point2D, b: Point2D) -> float:
    return (abs(a.x_mm - b.x_mm) + abs(a.y_mm - b.y_mm)) / 1000.0


def _estimate_radial_length_m(
    db: Point2D,
    rooms: list[Room],
    vertical_drop_m: float,
) -> float:
    """Deterministic Manhattan route estimate from DB1 through target room centroids."""
    if not rooms:
        return round(max(10.0, vertical_drop_m * 2.0), 1)
    ordered = sorted(rooms, key=lambda r: _manhattan_m(db, r.centroid_mm()))
    total = _manhattan_m(db, ordered[0].centroid_mm())
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        total += _manhattan_m(prev.centroid_mm(), nxt.centroid_mm())
    total += vertical_drop_m * max(1, min(len(ordered), 3))
    return round(max(8.0, total), 1)


def _estimate_ring_length_m(
    db: Point2D,
    rooms: list[Room],
    vertical_drop_m: float,
) -> float:
    """Deterministic closed Manhattan loop length estimate for a 32 A ring final."""
    if not rooms:
        return 30.0
    ordered = sorted(rooms, key=lambda r: (r.centroid_mm().x_mm, r.centroid_mm().y_mm))
    loop = _manhattan_m(db, ordered[0].centroid_mm())
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        loop += _manhattan_m(prev.centroid_mm(), nxt.centroid_mm())
    loop += _manhattan_m(ordered[-1].centroid_mm(), db)
    # Add perimeter socket drop allowance per served room
    loop += 2.0 * len(ordered) + vertical_drop_m
    return round(max(24.0, loop), 1)


def _chunk_by_max_area(rooms: list[Room], max_area_m2: float) -> list[list[Room]]:
    """Greedily partition rooms into deterministic chunks each with total area <= max_area_m2."""
    chunks: list[list[Room]] = []
    current: list[Room] = []
    current_area = 0.0
    for room in rooms:
        if current and current_area + room.area_m2 > max_area_m2 + 1e-6:
            chunks.append(current)
            current = [room]
            current_area = room.area_m2
        else:
            current.append(room)
            current_area += room.area_m2
    if current:
        chunks.append(current)
    return chunks


def _chunk_by_max_watts(
    rooms: list[Room],
    watts_by_room: dict[str, float],
    max_w: float,
) -> list[list[Room]]:
    chunks: list[list[Room]] = []
    current: list[Room] = []
    current_w = 0.0
    for room in rooms:
        rw = watts_by_room[room.id]
        if current and current_w + rw > max_w + 1e-6:
            chunks.append(current)
            current = [room]
            current_w = rw
        else:
            current.append(room)
            current_w += rw
    if current:
        chunks.append(current)
    return chunks


def _resolve_ambient_c(
    rooms: list[Room],
    standards: StandardsBundle,
    overrides: DesignOverrides | None,
) -> int:
    if overrides is not None and overrides.ambient_temp_c is not None:
        return overrides.ambient_temp_c
    if rooms and all(r.is_outdoor for r in rooms):
        return standards.eac_rules.ambient_defaults_c.roof_outdoor_c
    explicit = [r.ambient_temp_c for r in rooms if r.ambient_temp_c is not None]
    if explicit:
        return max(explicit)
    return standards.eac_rules.ambient_defaults_c.indoor_conduit_c


def _generate_board_circuits(
    *,
    rooms: list[Room],
    db: Point2D,
    board_id: str,
    floor_prefix: str,
    standards: StandardsBundle,
    overrides: DesignOverrides | None,
    next_id: list[int],
    emitted_keys: set[str],
    is_commercial: bool = False,
) -> list[CircuitSpec]:
    """Generate lighting, socket ring, split A/C, and appliance circuits for one board."""
    tmpl_std = standards.room_templates
    defaults = tmpl_std.circuit_defaults
    eac_std = standards.eac_rules
    u0 = eac_std.system_nominal.u0_v
    ul = eac_std.system_nominal.ul_v
    method: Literal["B", "C"] = eac_std.ambient_defaults_c.default_installation_method
    rcd_idn = eac_std.earthing.default_rcd_idn_ma

    circuits: list[CircuitSpec] = []

    def _next_id() -> str:
        cid = f"C{next_id[0]}"
        next_id[0] += 1
        return cid

    # 1. Lighting circuits (BS 7671 Reg 314.1: split across >= 2 circuits)
    lighting_w: dict[str, float] = {}
    day_rooms: list[Room] = []
    night_rooms: list[Room] = []

    for room in rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        est_w = max(room.area_m2 * rt.lighting_w_per_m2, rt.min_lighting_points * 40.0)
        lighting_w[room.id] = round(est_w, 1)
        if rt.lighting_zone == "day":
            day_rooms.append(room)
        else:
            night_rooms.append(room)

    if (not day_rooms or not night_rooms) and len(rooms) >= 2:
        midpoint = math.ceil(len(rooms) / 2)
        day_rooms = rooms[:midpoint]
        night_rooms = rooms[midpoint:]

    primary_light_lbl = (
        "Lighting - General / Task Zone" if is_commercial else "Lighting - Day / Living Zone"
    )
    secondary_light_lbl = (
        "Lighting - Circulation / Perimeter Zone"
        if is_commercial
        else "Lighting - Night / Sleeping Zone"
    )

    lighting_groups: list[tuple[str, list[Room]]] = []
    if day_rooms:
        day_chunks = _chunk_by_max_watts(
            day_rooms, lighting_w, defaults.lighting_circuit_max_connected_w
        )
        for i, chunk in enumerate(day_chunks, start=1):
            suffix = f" #{i}" if len(day_chunks) > 1 else ""
            lighting_groups.append((f"{floor_prefix}{primary_light_lbl}{suffix}", chunk))
    if night_rooms:
        night_chunks = _chunk_by_max_watts(
            night_rooms, lighting_w, defaults.lighting_circuit_max_connected_w
        )
        for i, chunk in enumerate(night_chunks, start=1):
            suffix = f" #{i}" if len(night_chunks) > 1 else ""
            lighting_groups.append((f"{floor_prefix}{secondary_light_lbl}{suffix}", chunk))

    for desc, grp_rooms in lighting_groups:
        conn_w = max(100.0, round(sum(lighting_w[r.id] for r in grp_rooms), 1))
        div_w = round(conn_w * defaults.lighting_diversity, 1)
        pf = defaults.lighting_power_factor
        ib = round(div_w / (u0 * pf), 2)
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                board_id=board_id,
                description=desc,
                category=CircuitCategory.LIGHTING,
                topology=CircuitTopology.RADIAL,
                room_ids=[r.id for r in grp_rooms],
                connected_load_w=conn_w,
                diversity_factor=defaults.lighting_diversity,
                diversified_load_w=div_w,
                power_factor=pf,
                design_current_ib_a=ib,
                nominal_voltage_v=u0,
                is_three_phase=False,
                length_m=_estimate_radial_length_m(
                    db, grp_rooms, defaults.vertical_drop_allowance_m
                ),
                installation_method=method,
                ambient_temp_c=_resolve_ambient_c(grp_rooms, standards, overrides),
                grouping_count=1,
                preferred_curve=BreakerCurve.B,
                min_breaker_in_a=6,
                min_cable_mm2=1.5,
                rcd_protected=True,
                rcd_idn_ma=rcd_idn,
                vd_limit_pct=eac_std.voltage_drop_limits_pct.lighting,
            )
        )

    # 2. 32 A Indoor Socket Ring Finals (BS 7671 Appendix 15, <= 100 m²/ring)
    #    + Dedicated 16 A Outdoor / Veranda IP65 Socket Radial (45°C, 2.5 mm² — avoids
    #      site-unrealistic 4.0 mm² upsizing of indoor BS 1363 ring finals!)
    kitchen_util_rooms: list[Room] = []
    general_socket_rooms: list[Room] = []
    outdoor_socket_rooms: list[Room] = []
    for room in rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        if rt.socket_group == "kitchen_utility":
            kitchen_util_rooms.append(room)
        elif rt.socket_group == "general":
            if room.is_outdoor:
                outdoor_socket_rooms.append(room)
            else:
                general_socket_rooms.append(room)

    if kitchen_util_rooms:
        ku_chunks = _chunk_by_max_area(kitchen_util_rooms, defaults.socket_ring_max_area_m2)
        for i, chunk in enumerate(ku_chunks, start=1):
            suffix = f" #{i}" if len(ku_chunks) > 1 else ""
            conn_w = defaults.kitchen_ring_connected_w
            div_w = round(conn_w * defaults.kitchen_ring_diversity, 1)
            pf = defaults.socket_power_factor
            ib = round(div_w / (u0 * pf), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=board_id,
                    description=f"{floor_prefix}Socket Ring Final - Kitchen & Utility{suffix}",
                    category=CircuitCategory.SOCKET_RING,
                    topology=CircuitTopology.RING_FINAL,
                    room_ids=[r.id for r in chunk],
                    connected_load_w=conn_w,
                    diversity_factor=defaults.kitchen_ring_diversity,
                    diversified_load_w=div_w,
                    power_factor=pf,
                    design_current_ib_a=ib,
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_ring_length_m(db, chunk, defaults.vertical_drop_allowance_m),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c(chunk, standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=32,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    if general_socket_rooms:
        gen_chunks = _chunk_by_max_area(general_socket_rooms, defaults.socket_ring_max_area_m2)
        for i, chunk in enumerate(gen_chunks, start=1):
            suffix = f" #{i}" if len(gen_chunks) > 1 else ""
            conn_w = defaults.general_ring_connected_w
            div_w = round(conn_w * defaults.general_ring_diversity, 1)
            pf = defaults.socket_power_factor
            ib = round(div_w / (u0 * pf), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=board_id,
                    description=f"{floor_prefix}Socket Ring Final - General{suffix}",
                    category=CircuitCategory.SOCKET_RING,
                    topology=CircuitTopology.RING_FINAL,
                    room_ids=[r.id for r in chunk],
                    connected_load_w=conn_w,
                    diversity_factor=defaults.general_ring_diversity,
                    diversified_load_w=div_w,
                    power_factor=pf,
                    design_current_ib_a=ib,
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_ring_length_m(db, chunk, defaults.vertical_drop_allowance_m),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c(chunk, standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=32,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    if outdoor_socket_rooms:
        out_conn_w = round(min(3000.0, max(1200.0, 900.0 * len(outdoor_socket_rooms))), 1)
        out_div_w = round(out_conn_w * 0.70, 1)
        pf = defaults.socket_power_factor
        out_ib = round(out_div_w / (u0 * pf), 2)
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                board_id=board_id,
                description=f"{floor_prefix}Outdoor / Veranda IP65 Socket Radial",
                category=CircuitCategory.SOCKET_RADIAL,
                topology=CircuitTopology.RADIAL,
                room_ids=[r.id for r in outdoor_socket_rooms],
                connected_load_w=out_conn_w,
                diversity_factor=0.70,
                diversified_load_w=out_div_w,
                power_factor=pf,
                design_current_ib_a=out_ib,
                nominal_voltage_v=u0,
                is_three_phase=False,
                length_m=_estimate_radial_length_m(
                    db, outdoor_socket_rooms, defaults.vertical_drop_allowance_m
                ),
                installation_method=method,
                ambient_temp_c=_resolve_ambient_c(outdoor_socket_rooms, standards, overrides),
                grouping_count=1,
                preferred_curve=BreakerCurve.B,
                min_breaker_in_a=16,
                min_cable_mm2=2.5,
                rcd_protected=True,
                rcd_idn_ma=rcd_idn,
                vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
            )
        )

    # 3. Dedicated Cyprus Split A/C Radials (Curve C, 0.85 diversity — when hvac_mode=split_ac)
    brief = (
        overrides.residential_brief
        if (overrides is not None and overrides.residential_brief is not None)
        else ResidentialSystemsBrief()
    )
    if is_commercial or brief.hvac_mode == "split_ac":
        for room in rooms:
            rt = tmpl_std.rooms[room.normalized_type]
            if not rt.has_split_ac or rt.ac_electrical_w <= 0.0:
                continue
            conn_w = rt.ac_electrical_w
            div_w = round(conn_w * defaults.split_ac_diversity, 1)
            pf = defaults.split_ac_power_factor
            ib = round(div_w / (u0 * pf), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=board_id,
                    description=f"{floor_prefix}Split A/C Unit - {room.raw_label}",
                    category=CircuitCategory.SPLIT_AC,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[room.id],
                    connected_load_w=conn_w,
                    diversity_factor=defaults.split_ac_diversity,
                    diversified_load_w=div_w,
                    power_factor=pf,
                    design_current_ib_a=ib,
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        db, [room], defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.C,
                    min_breaker_in_a=16,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    # 3b. Motorized Window Shutters per floor (residential Cyprus standard)
    if (not is_commercial) and brief.include_electric_shutters:
        shutter_rooms = [
            r
            for r in rooms
            if r.normalized_type
            in (
                RoomType.LIVING_ROOM,
                RoomType.KITCHEN_LIVING,
                RoomType.MASTER_BEDROOM,
                RoomType.BEDROOM,
                RoomType.KITCHEN,
            )
        ]
        if shutter_rooms:
            sh_w = round(min(1200.0, max(450.0, 150.0 * len(shutter_rooms))), 1)
            sh_div = round(sh_w * 0.30, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=board_id,
                    description=f"{floor_prefix}Motorized Window Shutters / Blinds",
                    category=CircuitCategory.SHUTTERS,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[r.id for r in shutter_rooms],
                    connected_load_w=sh_w,
                    diversity_factor=0.30,
                    diversified_load_w=sh_div,
                    power_factor=0.90,
                    design_current_ib_a=round(sh_div / (u0 * 0.90), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        db, shutter_rooms, defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c(shutter_rooms, standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=10,
                    min_cable_mm2=1.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    # 4. Dedicated Appliances (Cooker, Oven, Dishwasher, Washer, Solar WH, UPS, 3P Plant/Lift)
    for room in rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        for app in rt.dedicated_appliances:
            if (
                is_commercial
                and room.normalized_type == RoomType.KITCHEN
                and app.category in (CircuitCategory.COOKER, CircuitCategory.OVEN)
            ):
                # Commercial staff kitchenettes do not install 7 kW domestic hobs / built-in ovens
                continue
            if app.key in emitted_keys:
                continue
            emitted_keys.add(app.key)
            div_w = round(app.connected_w * app.diversity, 1)
            v_nom = ul if app.is_three_phase else u0
            ib = round(
                div_w / (math.sqrt(3.0) * v_nom * app.power_factor)
                if app.is_three_phase
                else div_w / (v_nom * app.power_factor),
                2,
            )
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=board_id,
                    description=f"{floor_prefix}{app.label} ({room.raw_label})",
                    category=app.category,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[room.id],
                    connected_load_w=app.connected_w,
                    diversity_factor=app.diversity,
                    diversified_load_w=div_w,
                    power_factor=app.power_factor,
                    design_current_ib_a=ib,
                    nominal_voltage_v=v_nom,
                    is_three_phase=app.is_three_phase,
                    length_m=_estimate_radial_length_m(
                        db, [room], defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=app.curve,
                    min_breaker_in_a=app.min_breaker_in_a,
                    min_cable_mm2=app.min_cable_mm2,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )
        if (
            is_commercial
            and room.normalized_type == RoomType.KITCHEN
            and "commercial_kitchenette_boiler" not in emitted_keys
        ):
            emitted_keys.add("commercial_kitchenette_boiler")
            kb_w = 2500.0
            kb_div = round(kb_w * 0.80, 1)
            kb_ib = round(kb_div / u0, 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=board_id,
                    description=(
                        f"{floor_prefix}Kitchenette Water Boiler / Coffee Radial ({room.raw_label})"
                    ),
                    category=CircuitCategory.APPLIANCE,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[room.id],
                    connected_load_w=kb_w,
                    diversity_factor=0.80,
                    diversified_load_w=kb_div,
                    power_factor=1.0,
                    design_current_ib_a=kb_ib,
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        db, [room], defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=16,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    return circuits


def generate_circuits(
    building: BuildingModel,
    standards: StandardsBundle,
    *,
    overrides: DesignOverrides | None = None,
) -> list[CircuitSpec]:
    """Deterministically generate BS 7671 + Cyprus EAC final circuits from a BuildingModel."""
    tmpl_std = standards.room_templates
    defaults = tmpl_std.circuit_defaults
    eac_std = standards.eac_rules
    u0 = eac_std.system_nominal.u0_v
    method: Literal["B", "C"] = eac_std.ambient_defaults_c.default_installation_method
    rcd_idn = eac_std.earthing.default_rcd_idn_ma
    is_commercial = is_commercial_building(building)

    next_id = [1]
    emitted_keys: set[str] = set()
    circuits: list[CircuitSpec] = []

    def _next_id() -> str:
        cid = f"C{next_id[0]}"
        next_id[0] += 1
        return cid

    if building.sub_boards:
        floor_to_board = {sb.floor: sb for sb in building.sub_boards}
        rooms_by_board: dict[str, list[Room]] = {sb.board_id: [] for sb in building.sub_boards}
        default_board = building.sub_boards[0]
        for room in building.rooms:
            target_sb = floor_to_board.get(room.floor, default_board)
            rooms_by_board[target_sb.board_id].append(room)

        for sb in building.sub_boards:
            b_rooms = rooms_by_board[sb.board_id]
            if not b_rooms:
                continue
            circuits.extend(
                _generate_board_circuits(
                    rooms=b_rooms,
                    db=sb.location_mm,
                    board_id=sb.board_id,
                    floor_prefix=f"[{sb.floor}] ",
                    standards=standards,
                    overrides=overrides,
                    next_id=next_id,
                    emitted_keys=emitted_keys,
                    is_commercial=is_commercial,
                )
            )
        primary_board_id = default_board.board_id
        primary_db = default_board.location_mm
    else:
        primary_board_id = building.db_anchor.id
        primary_db = building.db_anchor.location_mm
        circuits.extend(
            _generate_board_circuits(
                rooms=building.rooms,
                db=primary_db,
                board_id=primary_board_id,
                floor_prefix="",
                standards=standards,
                overrides=overrides,
                next_id=next_id,
                emitted_keys=emitted_keys,
                is_commercial=is_commercial,
            )
        )

    # Fallback: ensure Cyprus residential installations always include Immersion/Solar Water Heater
    if (not is_commercial) and ("solar_water_heater" not in emitted_keys):
        water_room = building.rooms[0]
        div_w = 3000.0
        ib = round(div_w / u0, 2)
        base_wh_len = _estimate_radial_length_m(
            primary_db, [water_room], defaults.vertical_drop_allowance_m
        )
        wh_roof_len = round(max(16.0, base_wh_len + 11.5), 2)
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                board_id=primary_board_id,
                description="Immersion / Solar Water Heater Booster (Roof Tank, 20A DP)",
                category=CircuitCategory.WATER_HEATER,
                topology=CircuitTopology.RADIAL,
                room_ids=[water_room.id],
                connected_load_w=3000.0,
                diversity_factor=1.0,
                diversified_load_w=div_w,
                power_factor=1.0,
                design_current_ib_a=ib,
                nominal_voltage_v=u0,
                is_three_phase=False,
                length_m=wh_roof_len,
                installation_method=method,
                ambient_temp_c=_resolve_ambient_c([water_room], standards, overrides),
                grouping_count=1,
                preferred_curve=BreakerCurve.B,
                min_breaker_in_a=16,
                min_cable_mm2=2.5,
                rcd_protected=True,
                rcd_idn_ma=rcd_idn,
                vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
            )
        )

    # 4b. Commercial Life-Safety Circuits (BS 5266 Emergency Lighting + BS 5839 Fire Alarm)
    if is_commercial:
        emg_rooms = [
            r
            for r in building.rooms
            if r.normalized_type
            in (RoomType.CORRIDOR, RoomType.STAIRWELL, RoomType.OPEN_OFFICE, RoomType.RETAIL_FLOOR)
        ] or building.rooms[:1]
        emg_w = 300.0
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                board_id=primary_board_id,
                description="Emergency Lighting (BS 5266 / EN 1838)",
                category=CircuitCategory.EMERGENCY_LIGHTING,
                topology=CircuitTopology.RADIAL,
                room_ids=[r.id for r in emg_rooms],
                connected_load_w=emg_w,
                diversity_factor=1.0,
                diversified_load_w=emg_w,
                power_factor=0.95,
                design_current_ib_a=round(emg_w / (u0 * 0.95), 2),
                nominal_voltage_v=u0,
                is_three_phase=False,
                length_m=_estimate_radial_length_m(
                    primary_db, emg_rooms, defaults.vertical_drop_allowance_m
                ),
                installation_method=method,
                ambient_temp_c=_resolve_ambient_c(emg_rooms, standards, overrides),
                grouping_count=1,
                preferred_curve=BreakerCurve.B,
                min_breaker_in_a=6,
                min_cable_mm2=1.5,
                rcd_protected=True,
                rcd_idn_ma=rcd_idn,
                vd_limit_pct=eac_std.voltage_drop_limits_pct.lighting,
            )
        )
        fa_w = 250.0
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                board_id=primary_board_id,
                description="Fire Alarm Panel Dedicated Spur (BS 5839 — Dedicated RCBO)",
                category=CircuitCategory.FIRE_ALARM,
                topology=CircuitTopology.RADIAL,
                room_ids=[building.rooms[0].id],
                connected_load_w=fa_w,
                diversity_factor=1.0,
                diversified_load_w=fa_w,
                power_factor=0.95,
                design_current_ib_a=round(fa_w / (u0 * 0.95), 2),
                nominal_voltage_v=u0,
                is_three_phase=False,
                length_m=12.0,
                installation_method=method,
                ambient_temp_c=_resolve_ambient_c(building.rooms[:1], standards, overrides),
                grouping_count=1,
                preferred_curve=BreakerCurve.B,
                min_breaker_in_a=6,
                min_cable_mm2=1.5,
                rcd_protected=True,
                rcd_idn_ma=rcd_idn,
                vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
            )
        )

    # 4c. Stage 5 Turnkey Cyprus Residential Plant, Life Safety, ELV, Site & KNX Circuits
    brief = (
        overrides.residential_brief
        if (overrides is not None and overrides.residential_brief is not None)
        else ResidentialSystemsBrief()
    )
    ul = eac_std.system_nominal.ul_v
    is_house = building.building_type == BuildingType.INDEPENDENT_HOUSE
    primary_room = building.rooms[0]
    utility_room = next(
        (
            r
            for r in building.rooms
            if r.normalized_type in (RoomType.UTILITY, RoomType.PLANT_ROOM, RoomType.CORRIDOR)
        ),
        primary_room,
    )
    outdoor_ambient = (
        overrides.ambient_temp_c
        if (overrides is not None and overrides.ambient_temp_c is not None)
        else eac_std.ambient_defaults_c.roof_outdoor_c
    )

    if not is_commercial:
        # Central 3P Air-to-Water Heat Pump + Underfloor Heating
        if brief.hvac_mode == "heat_pump_ufh":
            hp_w = round(brief.heat_pump_kw * 1000.0, 1)
            hp_div = round(hp_w * 0.90, 1)
            hp_ib = round(hp_div / (math.sqrt(3.0) * ul * 0.90), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description=(
                        f"Air-to-Water Heat Pump 3P Outdoor Unit ({brief.heat_pump_kw:g} kWe)"
                    ),
                    category=CircuitCategory.HEAT_PUMP,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[utility_room.id],
                    connected_load_w=hp_w,
                    diversity_factor=0.90,
                    diversified_load_w=hp_div,
                    power_factor=0.90,
                    design_current_ib_a=hp_ib,
                    nominal_voltage_v=ul,
                    is_three_phase=True,
                    length_m=18.0,
                    installation_method=method,
                    ambient_temp_c=outdoor_ambient,
                    grouping_count=1,
                    preferred_curve=BreakerCurve.C,
                    min_breaker_in_a=16,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )
            ufh_w = 1800.0
            ufh_div = round(ufh_w * 0.80, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="Heat Pump Hydrobox, UFH Manifolds & Room Thermostats",
                    category=CircuitCategory.HEAT_PUMP,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[utility_room.id],
                    connected_load_w=ufh_w,
                    diversity_factor=0.80,
                    diversified_load_w=ufh_div,
                    power_factor=0.95,
                    design_current_ib_a=round(ufh_div / (u0 * 0.95), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        primary_db, [utility_room], defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([utility_room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=16,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )
        elif brief.hvac_mode == "vrv":
            vrv_w = round(brief.vrv_outdoor_kw * 1000.0, 1)
            vrv_div = round(vrv_w * 0.85, 1)
            vrv_ib = round(vrv_div / (math.sqrt(3.0) * ul * 0.90), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description=f"VRV / VRF 3P Outdoor Condenser ({brief.vrv_outdoor_kw:g} kWe)",
                    category=CircuitCategory.HVAC_3P,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[utility_room.id],
                    connected_load_w=vrv_w,
                    diversity_factor=0.85,
                    diversified_load_w=vrv_div,
                    power_factor=0.90,
                    design_current_ib_a=vrv_ib,
                    nominal_voltage_v=ul,
                    is_three_phase=True,
                    length_m=20.0,
                    installation_method=method,
                    ambient_temp_c=outdoor_ambient,
                    grouping_count=1,
                    preferred_curve=BreakerCurve.C,
                    min_breaker_in_a=20,
                    min_cable_mm2=4.0,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )
            fcu_rooms = [
                r for r in building.rooms if tmpl_std.rooms[r.normalized_type].has_split_ac
            ] or [primary_room]
            fcu_w = round(max(400.0, 120.0 * len(fcu_rooms)), 1)
            fcu_div = round(fcu_w * 0.85, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="VRV Concealed Indoor Fan-Coil Units & Controllers",
                    category=CircuitCategory.SPLIT_AC,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[r.id for r in fcu_rooms],
                    connected_load_w=fcu_w,
                    diversity_factor=0.85,
                    diversified_load_w=fcu_div,
                    power_factor=0.92,
                    design_current_ib_a=round(fcu_div / (u0 * 0.92), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        primary_db, fcu_rooms, defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c(fcu_rooms, standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=10,
                    min_cable_mm2=1.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

        # Water Pressure Booster & Hot-Water Recirculation Pumps
        if brief.include_water_pumps:
            wp_w = 750.0
            wp_div = round(wp_w * 0.50, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="Water Pressure Booster & Hot-Water Recirculation Pumps",
                    category=CircuitCategory.WATER_PUMP,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[utility_room.id],
                    connected_load_w=wp_w,
                    diversity_factor=0.50,
                    diversified_load_w=wp_div,
                    power_factor=0.88,
                    design_current_ib_a=round(wp_div / (u0 * 0.88), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        primary_db, [utility_room], defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([utility_room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.C,
                    min_breaker_in_a=16,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

        # Sliding Yard Gate / Electric Garage Door Motor (houses)
        if brief.include_gate_motor and is_house:
            gt_w = 500.0
            gt_div = round(gt_w * 0.30, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="Sliding Yard Gate / Garage Door Motor & Photocells",
                    category=CircuitCategory.GATE_GARAGE,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[primary_room.id],
                    connected_load_w=gt_w,
                    diversity_factor=0.30,
                    diversified_load_w=gt_div,
                    power_factor=0.85,
                    design_current_ib_a=round(gt_div / (u0 * 0.85), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=max(15.0, brief.street_setback_m + 10.0),
                    installation_method=method,
                    ambient_temp_c=outdoor_ambient,
                    grouping_count=1,
                    preferred_curve=BreakerCurve.C,
                    min_breaker_in_a=10,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

        # BS 5839-6 Grade D1 Interlinked Residential Smoke & Kitchen Heat Detectors
        if brief.include_smoke_detectors:
            smk_w = 120.0
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="Interlinked Smoke & Kitchen Heat Detectors (BS 5839-6 Grade D1)",
                    category=CircuitCategory.SMOKE_DETECTION,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[r.id for r in building.rooms[:6]],
                    connected_load_w=smk_w,
                    diversity_factor=1.0,
                    diversified_load_w=smk_w,
                    power_factor=0.95,
                    design_current_ib_a=round(smk_w / (u0 * 0.95), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=_estimate_radial_length_m(
                        primary_db, building.rooms[:4], defaults.vertical_drop_allowance_m
                    ),
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([primary_room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=6,
                    min_cable_mm2=1.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.lighting,
                )
            )

        # ELV / Telecom Rack, Fiber ONT, PoE Switch, Intercom, Alarm & CCTV Power Spur
        if brief.include_elv:
            elv_w = 350.0
            elv_div = round(elv_w * 0.80, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="ELV Telecom Rack, PoE Switch, Intercom, Alarm & CCTV Spur",
                    category=CircuitCategory.ELV_POWER,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[utility_room.id],
                    connected_load_w=elv_w,
                    diversity_factor=0.80,
                    diversified_load_w=elv_div,
                    power_factor=0.95,
                    design_current_ib_a=round(elv_div / (u0 * 0.95), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=10.0,
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([utility_room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=16,
                    min_cable_mm2=2.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

        # External Garden & Perimeter Lighting (Photocell + Astro Timer, houses)
        if brief.include_site_works and is_house:
            grd_w = 400.0
            grd_div = round(grd_w * 0.80, 1)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="External Garden & Perimeter Lighting (Photocell / Astro Timer)",
                    category=CircuitCategory.LIGHTING,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[primary_room.id],
                    connected_load_w=grd_w,
                    diversity_factor=0.80,
                    diversified_load_w=grd_div,
                    power_factor=0.95,
                    design_current_ib_a=round(grd_div / (u0 * 0.95), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=max(20.0, brief.plot_length_m),
                    installation_method=method,
                    ambient_temp_c=outdoor_ambient,
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=10,
                    min_cable_mm2=1.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.lighting,
                )
            )

        # Optional Swimming Pool Sub-DB Feeder (BS 7671 Section 702)
        if brief.include_pool:
            pool_w = 4500.0
            pool_div = round(pool_w * 0.80, 1)
            pool_ib = round(pool_div / (math.sqrt(3.0) * ul * 0.88), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="Swimming Pool Sub-DB Feeder (BS 7671 Sec. 702, Pump + SELV 12V)",
                    category=CircuitCategory.POOL,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[primary_room.id],
                    connected_load_w=pool_w,
                    diversity_factor=0.80,
                    diversified_load_w=pool_div,
                    power_factor=0.88,
                    design_current_ib_a=pool_ib,
                    nominal_voltage_v=ul,
                    is_three_phase=True,
                    length_m=max(22.0, brief.plot_length_m * 0.85),
                    installation_method=method,
                    ambient_temp_c=outdoor_ambient,
                    grouping_count=1,
                    preferred_curve=BreakerCurve.C,
                    min_breaker_in_a=20,
                    min_cable_mm2=4.0,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

        # Optional Smart Home KNX Bus Power Supply (640mA) & DALI Dimming Gateway
        if brief.include_knx:
            knx_w = 200.0
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description="KNX Bus Power Supply (640mA), Actuators & DALI Gateway",
                    category=CircuitCategory.KNX_BUS,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[utility_room.id],
                    connected_load_w=knx_w,
                    diversity_factor=1.0,
                    diversified_load_w=knx_w,
                    power_factor=0.95,
                    design_current_ib_a=round(knx_w / (u0 * 0.95), 2),
                    nominal_voltage_v=u0,
                    is_three_phase=False,
                    length_m=8.0,
                    installation_method=method,
                    ambient_temp_c=_resolve_ambient_c([utility_room], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.B,
                    min_breaker_in_a=10,
                    min_cable_mm2=1.5,
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    # 5. Optional Dedicated EV Charger (BS 7671 Section 722, Mode 3)
    effective_ev_kw = (
        brief.ev_charger_kw if brief.ev_charger_kw is not None else building.ev_charger_kw
    )
    if effective_ev_kw > 0.0:
        ev_w = round(effective_ev_kw * 1000.0, 1)
        is_3p_ev = effective_ev_kw > 7.4
        ev_v = eac_std.system_nominal.ul_v if is_3p_ev else u0
        ev_ib = round(
            ev_w / (math.sqrt(3.0) * ev_v) if is_3p_ev else ev_w / ev_v,
            2,
        )
        ev_ambient = (
            overrides.ambient_temp_c
            if (overrides is not None and overrides.ambient_temp_c is not None)
            else eac_std.ambient_defaults_c.roof_outdoor_c
        )
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                board_id=primary_board_id,
                description=f"EV Charger Mode 3 ({effective_ev_kw:g} kW)",
                category=CircuitCategory.EV_CHARGER,
                topology=CircuitTopology.RADIAL,
                room_ids=[building.rooms[0].id],
                connected_load_w=ev_w,
                diversity_factor=1.0,
                diversified_load_w=ev_w,
                power_factor=1.0,
                design_current_ib_a=ev_ib,
                nominal_voltage_v=ev_v,
                is_three_phase=is_3p_ev,
                length_m=18.0,
                installation_method=method,
                ambient_temp_c=ev_ambient,
                grouping_count=1,
                preferred_curve=BreakerCurve.B,
                min_breaker_in_a=32 if not is_3p_ev else 16,
                min_cable_mm2=6.0 if not is_3p_ev else 4.0,
                rcd_protected=True,
                rcd_idn_ma=rcd_idn,
                vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
            )
        )

    # 6. Optional Commercial 3-Phase Central HVAC / Process Feeder(s)
    if building.extra_three_phase_load_kva > 0.0:
        total_extra = building.extra_three_phase_load_kva
        ul = eac_std.system_nominal.ul_v
        chunk_max = 60.0 if total_extra > 120.0 else 30.0
        n_feeders = max(1, math.ceil(total_extra / chunk_max))
        each_kva = round(total_extra / n_feeders, 3)
        for feeder_idx in range(1, n_feeders + 1):
            pf = 0.90
            watts = round(each_kva * 1000.0 * pf, 1)
            ib_3p = round((each_kva * 1000.0) / (math.sqrt(3.0) * ul), 2)
            use_mccb = ib_3p > 63.0
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    board_id=primary_board_id,
                    description=f"3-Phase Commercial HVAC / VRF Feeder #{feeder_idx}",
                    category=CircuitCategory.HVAC_3P,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[building.rooms[0].id],
                    connected_load_w=watts,
                    diversity_factor=1.0,
                    diversified_load_w=watts,
                    power_factor=pf,
                    design_current_ib_a=ib_3p,
                    nominal_voltage_v=ul,
                    is_three_phase=True,
                    length_m=22.0,
                    installation_method=method,
                    insulation_type="xlpe_90c" if use_mccb else "pvc_70c",
                    ambient_temp_c=_resolve_ambient_c(building.rooms[:1], standards, overrides),
                    grouping_count=1,
                    preferred_curve=BreakerCurve.MCCB if use_mccb else BreakerCurve.C,
                    min_breaker_in_a=80 if use_mccb else (32 if each_kva >= 15.0 else 16),
                    min_cable_mm2=25.0 if use_mccb else (6.0 if each_kva >= 15.0 else 4.0),
                    rcd_protected=True,
                    rcd_idn_ma=rcd_idn,
                    vd_limit_pct=eac_std.voltage_drop_limits_pct.power,
                )
            )

    return circuits


def _spatial_halves(rooms: list[Room]) -> tuple[list[Room], list[Room]]:
    """Split rooms into two spatially compact halves along their dominant centroid axis."""
    xs = [r.centroid_mm().x_mm for r in rooms]
    ys = [r.centroid_mm().y_mm for r in rooms]
    use_x = (max(xs) - min(xs)) >= (max(ys) - min(ys))
    ordered = sorted(
        rooms,
        key=lambda r: (
            (r.centroid_mm().x_mm, r.centroid_mm().y_mm)
            if use_x
            else (r.centroid_mm().y_mm, r.centroid_mm().x_mm)
        ),
    )
    mid = max(1, len(ordered) // 2)
    return ordered[:mid], ordered[mid:]


def split_oversized_routed_circuits(
    building: BuildingModel,
    specs: list[CircuitSpec],
    routed_lengths_m: dict[str, float],
    standards: StandardsBundle,
    *,
    overrides: DesignOverrides | None = None,
    max_lighting_length_m: float = 95.0,
    max_ring_length_m: float = 120.0,
) -> tuple[list[CircuitSpec], list[ReviewFlag], bool]:
    """Auto-split over-long multi-room lighting (>95 m) and ring (>120 m) circuits after routing.

    Keeps 6 A lighting circuits on installable 1.5 mm² terminals (avoiding 4.0 mm² ceiling-rose
    clashes) and keeps 32 A socket rings within the practical BS 7671 Appendix 15 length guideline
    while conserving total diversified kVA across split rings.
    """
    rooms_by_id = {r.id: r for r in building.rooms}
    boards_by_id = {sb.board_id: sb.location_mm for sb in building.sub_boards}
    default_db = building.db_anchor.location_mm
    tmpl_std = standards.room_templates
    defaults = tmpl_std.circuit_defaults
    u0 = standards.eac_rules.system_nominal.u0_v
    protected_ids = set(overrides.circuit_overrides.keys()) if overrides is not None else set()

    new_specs: list[CircuitSpec] = []
    flags: list[ReviewFlag] = []
    did_split = False

    for spec in specs:
        actual_len = routed_lengths_m.get(spec.circuit_id, spec.length_m)
        spec_rooms = [rooms_by_id[rid] for rid in spec.room_ids if rid in rooms_by_id]
        db_pt = boards_by_id.get(spec.board_id, default_db)

        should_split_light = (
            spec.category == CircuitCategory.LIGHTING
            and len(spec_rooms) >= 2
            and actual_len > max_lighting_length_m
            and spec.circuit_id not in protected_ids
        )
        should_split_ring = (
            spec.category == CircuitCategory.SOCKET_RING
            and len(spec_rooms) >= 2
            and actual_len > max_ring_length_m
            and spec.circuit_id not in protected_ids
        )

        if not (should_split_light or should_split_ring):
            new_specs.append(spec)
            continue

        did_split = True
        left_rooms, right_rooms = _spatial_halves(spec_rooms)
        base_desc = spec.description.split(" (Split")[0]

        for part_idx, part_rooms in enumerate((left_rooms, right_rooms), start=1):
            if should_split_light:
                conn_w = max(
                    100.0,
                    round(
                        sum(
                            max(
                                r.area_m2 * tmpl_std.rooms[r.normalized_type].lighting_w_per_m2,
                                tmpl_std.rooms[r.normalized_type].min_lighting_points * 40.0,
                            )
                            for r in part_rooms
                        ),
                        1,
                    ),
                )
                div_w = round(conn_w * spec.diversity_factor, 1)
                ib = round(div_w / (u0 * spec.power_factor), 2)
                est_len = _estimate_radial_length_m(
                    db_pt, part_rooms, defaults.vertical_drop_allowance_m
                )
            else:
                # Conserve building diversified demand across split rings (floor 2300 W/ring)
                share = len(part_rooms) / max(1, len(spec_rooms))
                conn_w = max(2300.0, round(spec.connected_load_w * share, 1))
                div_w = round(conn_w * spec.diversity_factor, 1)
                ib = round(div_w / (u0 * spec.power_factor), 2)
                est_len = _estimate_ring_length_m(
                    db_pt, part_rooms, defaults.vertical_drop_allowance_m
                )

            new_specs.append(
                spec.model_copy(
                    update={
                        "description": f"{base_desc} (Split {part_idx})",
                        "room_ids": [r.id for r in part_rooms],
                        "connected_load_w": conn_w,
                        "diversified_load_w": div_w,
                        "design_current_ib_a": ib,
                        "length_m": est_len,
                        "ambient_temp_c": _resolve_ambient_c(part_rooms, standards, overrides),
                    }
                )
            )

        limit_m = max_lighting_length_m if should_split_light else max_ring_length_m
        flags.append(
            ReviewFlag(
                code="CIRCUIT_AUTO_SPLIT_FOR_PRACTICABILITY",
                severity=ReviewSeverity.INFO,
                category="bs7671",
                regulation_ref="BS 7671 Reg 314.1 & Appendix 15",
                message=(
                    f"Auto-split oversized {spec.category.value} circuit '{spec.description}' "
                    f"(routed {actual_len:.1f} m > {limit_m:.0f} m practical limit) into 2 "
                    "spatially compact circuits to preserve standard terminal/conductor sizing."
                ),
            )
        )

    if not did_split:
        return specs, [], False

    # Deterministically renumber C1..CN unless user supplied circuit overrides
    if not protected_ids:
        renumbered = [
            s.model_copy(update={"circuit_id": f"C{idx}"})
            for idx, s in enumerate(new_specs, start=1)
        ]
        return renumbered, flags, True

    used_ids: set[str] = set()
    next_num = len(new_specs) + 1
    out: list[CircuitSpec] = []
    for s in new_specs:
        if s.circuit_id not in used_ids:
            used_ids.add(s.circuit_id)
            out.append(s)
        else:
            while f"C{next_num}" in used_ids or f"C{next_num}" in protected_ids:
                next_num += 1
            cid = f"C{next_num}"
            used_ids.add(cid)
            out.append(s.model_copy(update={"circuit_id": cid}))
    return out, flags, True
