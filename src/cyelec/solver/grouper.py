"""Deterministic final circuit grouper per BS 7671 Reg 314.1, Appendix 15 & Cyprus EAC practice."""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.building import BuildingModel, Point2D, Room
from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitSpec,
    CircuitTopology,
    DesignOverrides,
)
from cyelec.standards_loader import StandardsBundle


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
    db = building.db_anchor.location_mm

    circuits: list[CircuitSpec] = []
    next_idx = 1

    def _next_id() -> str:
        nonlocal next_idx
        cid = f"C{next_idx}"
        next_idx += 1
        return cid

    # ------------------------------------------------------------------
    # 1. Lighting circuits (BS 7671 Reg 314.1: split across >= 2 circuits)
    # ------------------------------------------------------------------
    lighting_w: dict[str, float] = {}
    day_rooms: list[Room] = []
    night_rooms: list[Room] = []

    for room in building.rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        est_w = max(room.area_m2 * rt.lighting_w_per_m2, rt.min_lighting_points * 40.0)
        lighting_w[room.id] = round(est_w, 1)
        if rt.lighting_zone == "day":
            day_rooms.append(room)
        else:
            night_rooms.append(room)

    # Guarantee >= 2 lighting circuits whenever >= 2 rooms exist (Reg 314.1)
    if (not day_rooms or not night_rooms) and len(building.rooms) >= 2:
        midpoint = math.ceil(len(building.rooms) / 2)
        day_rooms = building.rooms[:midpoint]
        night_rooms = building.rooms[midpoint:]

    lighting_groups: list[tuple[str, list[Room]]] = []
    if day_rooms:
        day_chunks = _chunk_by_max_watts(
            day_rooms, lighting_w, defaults.lighting_circuit_max_connected_w
        )
        for i, chunk in enumerate(day_chunks, start=1):
            suffix = f" #{i}" if len(day_chunks) > 1 else ""
            lighting_groups.append((f"Lighting - Day / Living Zone{suffix}", chunk))
    if night_rooms:
        night_chunks = _chunk_by_max_watts(
            night_rooms, lighting_w, defaults.lighting_circuit_max_connected_w
        )
        for i, chunk in enumerate(night_chunks, start=1):
            suffix = f" #{i}" if len(night_chunks) > 1 else ""
            lighting_groups.append((f"Lighting - Night / Sleeping Zone{suffix}", chunk))

    for desc, grp_rooms in lighting_groups:
        conn_w = max(100.0, round(sum(lighting_w[r.id] for r in grp_rooms), 1))
        div_w = round(conn_w * defaults.lighting_diversity, 1)
        pf = defaults.lighting_power_factor
        ib = round(div_w / (u0 * pf), 2)
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
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

    # ------------------------------------------------------------------
    # 2. 32 A Socket Ring Finals (BS 7671 Appendix 15, <= 100 m²/ring)
    # ------------------------------------------------------------------
    kitchen_util_rooms: list[Room] = []
    general_socket_rooms: list[Room] = []
    for room in building.rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        if rt.socket_group == "kitchen_utility":
            kitchen_util_rooms.append(room)
        elif rt.socket_group == "general":
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
                    description=f"Socket Ring Final - Kitchen & Utility{suffix}",
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
                    length_m=_estimate_ring_length_m(
                        db, chunk, defaults.vertical_drop_allowance_m
                    ),
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
                    description=f"Socket Ring Final - General{suffix}",
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
                    length_m=_estimate_ring_length_m(
                        db, chunk, defaults.vertical_drop_allowance_m
                    ),
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

    # ------------------------------------------------------------------
    # 3. Dedicated Cyprus Split A/C Radials (Curve C, 0.85 summer diversity)
    # ------------------------------------------------------------------
    for room in building.rooms:
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
                description=f"Split A/C Unit - {room.raw_label}",
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

    # ------------------------------------------------------------------
    # 4. Dedicated Appliances (Cooker, Oven, Dishwasher, Washer, Solar WH)
    # ------------------------------------------------------------------
    emitted_keys: set[str] = set()
    for room in building.rooms:
        rt = tmpl_std.rooms[room.normalized_type]
        for app in rt.dedicated_appliances:
            if app.key in emitted_keys:
                continue
            emitted_keys.add(app.key)
            div_w = round(app.connected_w * app.diversity, 1)
            ib = round(div_w / (u0 * app.power_factor), 2)
            circuits.append(
                CircuitSpec(
                    circuit_id=_next_id(),
                    description=f"{app.label} ({room.raw_label})",
                    category=app.category,
                    topology=CircuitTopology.RADIAL,
                    room_ids=[room.id],
                    connected_load_w=app.connected_w,
                    diversity_factor=app.diversity,
                    diversified_load_w=div_w,
                    power_factor=app.power_factor,
                    design_current_ib_a=ib,
                    nominal_voltage_v=u0,
                    is_three_phase=False,
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

    # Fallback: ensure Cyprus residential installations always include Immersion/Solar Water Heater
    if "solar_water_heater" not in emitted_keys:
        water_room = building.rooms[0]
        div_w = 3000.0
        ib = round(div_w / u0, 2)
        circuits.append(
            CircuitSpec(
                circuit_id=_next_id(),
                description="Immersion / Solar Water Heater Booster",
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
                length_m=_estimate_radial_length_m(
                    db, [water_room], defaults.vertical_drop_allowance_m
                ),
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

    return circuits
