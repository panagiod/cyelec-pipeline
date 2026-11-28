"""Residential engineering calculators: CIBSE Lumen Method, 18mm DIN-Rail DB & IEC 62305-2 LPS."""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.building import BuildingModel, BuildingType, RoomType
from cyelec.models.electrical import (
    BoardCircuitLegendRow,
    BoardModuleSlot,
    CircuitCategory,
    DinRailBoardLayout,
    DinRailRow,
    ElectricalDesignResult,
    LightningRiskAssessment,
    PhaseAssignment,
    PvStorageEngineeringResult,
    ResidentialSystemsBrief,
    ReviewFlag,
    ReviewSeverity,
    RoomLumenCheck,
    SolvedCircuit,
)
from cyelec.models.spatial import DeviceType, PlacedDevice

_LUM_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.LUMINAIRE,
        DeviceType.PENDANT_LIGHT,
        DeviceType.WALL_SCONCE,
        DeviceType.LINEAR_COVE,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.MIRROR_LIGHT,
    }
)

_TARGET_LUX_BY_ROOM: dict[RoomType, int] = {
    RoomType.LIVING_ROOM: 150,
    RoomType.KITCHEN: 300,
    RoomType.KITCHEN_LIVING: 200,
    RoomType.MASTER_BEDROOM: 150,
    RoomType.BEDROOM: 150,
    RoomType.BATHROOM: 200,
    RoomType.ENSUITE: 200,
    RoomType.WC: 150,
    RoomType.CORRIDOR: 100,
    RoomType.UTILITY: 200,
    RoomType.VERANDA: 50,
    RoomType.STORAGE: 100,
    RoomType.OPEN_OFFICE: 500,
    RoomType.MEETING_ROOM: 500,
    RoomType.SERVER_ROOM: 300,
    RoomType.RETAIL_FLOOR: 300,
    RoomType.WAREHOUSE: 200,
    RoomType.PLANT_ROOM: 200,
    RoomType.STAIRWELL: 150,
    RoomType.LIFT_SHAFT: 100,
}

# Standard architectural LED efficacy (lm/W) used for Lumen Method verification
_LED_EFFICACY_LM_PER_W = 105.0
_MAINTENANCE_FACTOR = 0.80


def _utilization_factor_from_room(area_m2: float, ceiling_height_mm: float) -> float:
    """Compute CIBSE Utilization Factor (UF) from Room Index K = (L*W)/(Hm*(L+W))."""
    side_m = math.sqrt(max(area_m2, 1.0))
    hm = max(0.8, (ceiling_height_mm / 1000.0) - 0.80)  # Mounting height above 0.80m working plane
    room_index_k = area_m2 / max(hm * 2.0 * side_m, 0.5)
    # Calibrated residential/commercial ceiling/wall reflectance (75/55/20) LED downlight UF curve
    uf = 0.42 + 0.36 * (1.0 - math.exp(-0.80 * room_index_k))
    return round(min(0.80, max(0.38, uf)), 2)


_BASE_FIXTURE_WATTS: dict[RoomType, float] = {
    RoomType.LIVING_ROOM: 18.0,
    RoomType.KITCHEN: 20.0,
    RoomType.KITCHEN_LIVING: 20.0,
    RoomType.MASTER_BEDROOM: 15.0,
    RoomType.BEDROOM: 15.0,
    RoomType.BATHROOM: 15.0,
    RoomType.ENSUITE: 15.0,
    RoomType.WC: 12.0,
    RoomType.CORRIDOR: 12.0,
    RoomType.UTILITY: 15.0,
    RoomType.VERANDA: 12.0,
    RoomType.STORAGE: 12.0,
    RoomType.OPEN_OFFICE: 40.0,
    RoomType.MEETING_ROOM: 40.0,
    RoomType.SERVER_ROOM: 28.0,
    RoomType.RETAIL_FLOOR: 40.0,
    RoomType.WAREHOUSE: 50.0,
    RoomType.PLANT_ROOM: 24.0,
    RoomType.STAIRWELL: 15.0,
    RoomType.LIFT_SHAFT: 15.0,
}


def size_room_luminaires(
    room_type: RoomType,
    area_m2: float,
    ceiling_height_mm: float,
    min_points: int = 1,
) -> tuple[int, float, int, int]:
    """Size luminaire count, watts, lumens & target lux via CIBSE / BS EN 12464-1 Lumen Method."""
    target_lux = _TARGET_LUX_BY_ROOM.get(room_type, 150)
    watts = _BASE_FIXTURE_WATTS.get(room_type, 15.0)
    # Step up fixture lumen package for very large halls/open-plan spaces (>55 m²)
    if area_m2 >= 55.0 and watts < 24.0:
        watts = 24.0
    uf = _utilization_factor_from_room(area_m2, ceiling_height_mm)
    mf = _MAINTENANCE_FACTOR
    lumens = int(round(watts * _LED_EFFICACY_LM_PER_W))
    eff_lm_per_fix = max(lumens * uf * mf, 100.0)
    needed = max(1, min_points, int(math.ceil((target_lux * area_m2) / eff_lm_per_fix)))
    max_fixtures = 24 if area_m2 >= 60.0 else 16
    if needed > max_fixtures:
        # Scale fixture lumen output (e.g. 60W commercial linear / high-output LED panel)
        req_total_lm = (target_lux * area_m2) / max(uf * mf, 0.35)
        lumens = int(math.ceil(req_total_lm / max_fixtures / 50.0) * 50)
        watts = round(lumens / _LED_EFFICACY_LM_PER_W, 1)
        needed = max_fixtures
    return needed, watts, lumens, target_lux


def compute_room_lumen_checks(
    building: BuildingModel,
    devices: list[PlacedDevice],
) -> tuple[list[RoomLumenCheck], list[ReviewFlag]]:
    """Verify room-by-room illuminance via 2D point-by-point BS EN 12464-1 grid physics."""
    from cyelec.solver.photometrics import evaluate_room_photometrics_2d

    checks: list[RoomLumenCheck] = []
    flags: list[ReviewFlag] = []

    devs_by_room: dict[str, list[PlacedDevice]] = {}
    for d in devices:
        if d.device_type in _LUM_DEVICE_TYPES:
            devs_by_room.setdefault(d.room_id, []).append(d)

    for room in building.rooms:
        lums = devs_by_room.get(room.id, [])
        if not lums:
            continue
        n_fix = len(lums)
        total_w = sum(float(d.luminaire_wattage_w or 15.0) for d in lums)
        avg_w = round(total_w / n_fix, 1)
        total_lm = sum(
            int(d.lumens or round(float(d.luminaire_wattage_w or 15.0) * _LED_EFFICACY_LM_PER_W))
            for d in lums
        )
        avg_lm = max(100, int(round(total_lm / n_fix)))
        uf = _utilization_factor_from_room(room.area_m2, room.ceiling_height_mm)
        mf = _MAINTENANCE_FACTOR
        grid_res = evaluate_room_photometrics_2d(room, lums, maintenance_factor=mf)
        achieved_lux = grid_res.avg_lux
        target_lux = _TARGET_LUX_BY_ROOM.get(room.normalized_type, 150)
        # Allow 10% design tolerance on residential average illuminance checks
        compliant = achieved_lux >= (target_lux * 0.90)
        primary_type = lums[0].luminaire_fixture_type or (
            "IP44 Downlight + Mirror" if room.is_wet_room else "Recessed LED Downlight"
        )
        checks.append(
            RoomLumenCheck(
                room_id=room.id,
                room_label=room.raw_label,
                floor=room.floor,
                area_m2=round(room.area_m2, 2),
                fixture_type=primary_type,
                luminaire_count=n_fix,
                watts_per_fixture=avg_w,
                lumens_per_fixture=avg_lm,
                target_lux=target_lux,
                achieved_lux=achieved_lux,
                min_lux=grid_res.min_lux,
                max_lux=grid_res.max_lux,
                uniformity_u0=grid_res.uniformity_u0,
                u0_target=grid_res.u0_target,
                u0_compliant=grid_res.u0_compliant,
                grid_points_evaluated=grid_res.grid_points,
                utilization_factor=uf,
                maintenance_factor=mf,
                compliant=compliant,
            )
        )
        if not compliant:
            flags.append(
                ReviewFlag(
                    code="PHOTOMETRIC_LUX_BELOW_TARGET",
                    severity=ReviewSeverity.INFO,
                    category="geometry",
                    regulation_ref="BS EN 12464-1 / CIBSE SLL Code for Lighting",
                    message=(
                        f"Room '{room.raw_label}' ({room.area_m2:.1f} m²): 2D grid E_avg="
                        f"{achieved_lux:.0f} lx (E_min={grid_res.min_lux:.0f} lx, "
                        f"U0={grid_res.uniformity_u0:.2f}) vs {target_lux} lx target "
                        f"({n_fix}x {avg_w:g}W LED). Increase lumen output or add task lighting."
                    ),
                    requires_etek_signoff=False,
                )
            )
        elif not grid_res.u0_compliant:
            flags.append(
                ReviewFlag(
                    code="PHOTOMETRIC_UNIFORMITY_LOW",
                    severity=ReviewSeverity.INFO,
                    category="geometry",
                    regulation_ref="BS EN 12464-1 (Illuminance Uniformity U0 = Emin/Eavg)",
                    message=(
                        f"Room '{room.raw_label}' ({room.area_m2:.1f} m²): 2D working-plane "
                        f"U0={grid_res.uniformity_u0:.2f} below {grid_res.u0_target:.2f} "
                        f"target (E_min={grid_res.min_lux:.0f} lx, E_avg={achieved_lux:.0f} lx). "
                        "Consider wider beam optics or splitting into evenly spaced downlights."
                    ),
                    requires_etek_signoff=False,
                )
            )

    return checks, flags


_COMMERCIAL_BUILDING_TYPES: frozenset[BuildingType] = frozenset(
    {
        BuildingType.OFFICE,
        BuildingType.SHOP,
        BuildingType.WORKSHOP,
        BuildingType.INDUSTRIAL,
    }
)

_SlotKind = Literal[
    "incomer",
    "rcd_s",
    "spd",
    "pv_isol",
    "feeder",
    "rcbo",
    "knx",
    "ats",
    "meter",
    "spare",
]
_SlotPhase = Literal["L1", "L2", "L3", "3P", "N/A"]


def _device_power_loss_w(kind: _SlotKind, rating_a: int | None, is_3p: bool) -> float:
    """Typical IEC 60890 / catalog power dissipation (W) at nominal load."""
    r = rating_a or 16
    if kind == "spare":
        return 0.0
    if kind == "spd":
        return 0.5
    if kind == "meter":
        return 3.0
    if kind == "knx":
        return 4.5
    if kind == "ats":
        return 6.0
    if kind == "rcd_s":
        return 3.8 if is_3p else 2.2
    if kind == "incomer":
        if r >= 160:
            return 18.0
        if r >= 100:
            return 9.0
        return 4.8 if is_3p else 2.6
    if kind in ("feeder", "pv_isol"):
        if r >= 100:
            return 12.0
        if r >= 63:
            return 7.5 if is_3p else 4.2
        return 5.0 if is_3p else 2.8
    # Final RCBO
    if is_3p:
        return round(4.5 + 0.14 * r, 1)
    if r <= 10:
        return 1.5
    if r <= 20:
        return 2.4
    return 3.8


def _phase_literal(phase: PhaseAssignment) -> _SlotPhase:
    if phase == PhaseAssignment.L1:
        return "L1"
    if phase == PhaseAssignment.L2:
        return "L2"
    if phase == PhaseAssignment.L3:
        return "L3"
    return "3P"


def _board_phase_loads_a(circuits: list[SolvedCircuit]) -> dict[str, float]:
    """Sum diversified design current per phase (A) for a board's circuits."""
    acc = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    for c in circuits:
        ib = c.spec.design_current_ib_a
        if c.phase == PhaseAssignment.THREE_PHASE:
            acc["L1"] += ib
            acc["L2"] += ib
            acc["L3"] += ib
        else:
            acc[c.phase.value] += ib
    return {k: round(v, 1) for k, v in acc.items()}


def _pack_din_slots(
    raw_slots: list[tuple[str, BoardModuleSlot]],
    *,
    modules_per_row: int = 18,
    min_spare_ratio: float = 0.20,
    min_rows: int = 2,
) -> tuple[list[DinRailRow], int, int, int, float]:
    """Pack physical DIN/MCCB device slots onto rails preserving >=20% spare capacity."""
    used_total = sum(sl.width_m for _, sl in raw_slots)
    req_capacity = int(math.ceil(used_total / max(1.0 - min_spare_ratio, 0.5)))
    mpr = modules_per_row
    if mpr == 18 and req_capacity > 72:
        mpr = 24
    num_rows = max(min_rows, int(math.ceil(req_capacity / float(mpr))))

    rows: list[DinRailRow] = []
    cur_items: list[str] = []
    cur_slots: list[BoardModuleSlot] = []
    cur_used = 0
    row_no = 1

    def _flush_row() -> None:
        nonlocal row_no, cur_items, cur_slots, cur_used
        trail_spare = mpr - cur_used
        final_slots = list(cur_slots)
        if trail_spare > 0:
            final_slots.append(
                BoardModuleSlot(
                    slot_start_m=cur_used + 1,
                    width_m=trail_spare,
                    kind="spare",
                    code="SPARE",
                    label=f"{trail_spare}M",
                    phase="N/A",
                    power_loss_w=0.0,
                )
            )
        rows.append(
            DinRailRow(
                row_no=row_no,
                modules_used=cur_used,
                modules_capacity=mpr,
                items=cur_items,
                slots=final_slots,
            )
        )
        row_no += 1
        cur_items = []
        cur_slots = []
        cur_used = 0

    for item_text, sl in raw_slots:
        if cur_used + sl.width_m > mpr and cur_items:
            _flush_row()
        placed = sl.model_copy(update={"slot_start_m": cur_used + 1})
        cur_items.append(f"{item_text} ({sl.width_m}M)")
        cur_slots.append(placed)
        cur_used += sl.width_m

    if cur_items:
        _flush_row()

    while len(rows) < num_rows:
        rno = len(rows) + 1
        rows.append(
            DinRailRow(
                row_no=rno,
                modules_used=0,
                modules_capacity=mpr,
                items=["SPARE RAIL (20%+ Expansion)"],
                slots=[
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=mpr,
                        kind="spare",
                        code="SPARE",
                        label=f"SPARE RAIL ({mpr}M)",
                        phase="N/A",
                        power_loss_w=0.0,
                    )
                ],
            )
        )

    total_cap = len(rows) * mpr
    spare = max(0, total_cap - used_total)
    total_loss_w = round(sum(sl.power_loss_w for r in rows for sl in r.slots), 1)
    return rows, used_total, spare, total_cap, total_loss_w


def _enclosure_catalog_spec(
    n_rows: int,
    mpr: int,
    spare_pct: float,
    busbar_req_a: int,
    *,
    is_panelboard_61439_2: bool = False,
    is_commercial: bool = False,
    is_outdoor_ip65: bool = False,
) -> tuple[str, str, float, str, str, int, str]:
    """Return (enclosure_spec, dims_mm, max_diss_w, ip_rating, std_ref, busbar_a, term_bars)."""
    cap = n_rows * mpr
    if is_outdoor_ip65:
        busbar_a = 63
        dims = "310 x 440 x 155 mm" if cap <= 24 else "380 x 560 x 170 mm"
        max_diss = 48.0 if cap <= 24 else 72.0
        ip = "IP65 / IK08"
        std_ref = "IEC 61439-3 / BS 7671 Sec. 702 (Outdoor Pool Plant)"
        enc = (
            f"Weatherproof UV-Stabilized GRP/Polycarbonate Enclosure, "
            f"{n_rows}x{mpr}M ({cap}M total, {spare_pct:.0f}% spare), IP65/IK08"
        )
        bars = (
            "63A 3P+N Comb Busbar + Brass N/PE Bars + BS 7671 Sec. 702 "
            "Supplementary Equipotential Bonding Bar (6mm² Cu)"
        )
        return enc, dims, max_diss, ip, std_ref, busbar_a, bars

    if is_panelboard_61439_2:
        busbar_a = 400 if busbar_req_a > 250 else 250
        dims = "800 x 1200 x 250 mm" if busbar_a <= 250 else "800 x 1600 x 300 mm"
        max_diss = 260.0 if busbar_a <= 250 else 360.0
        ip = "IP41 / IK09"
        std_ref = "IEC 61439-2 Form 2b MCCB Panelboard"
        enc = (
            f"Powder-Coated Steel MCCB Panelboard (IEC 61439-2 Form 2b, {busbar_a}A Cu Busbars), "
            f"{n_rows}x{mpr}M ({cap}M equiv, {spare_pct:.0f}% spare), IP41"
        )
        bars = (
            f"{busbar_a}A Tin-Plated 3P+N Copper Busbar Stack (Icw 25kA/1s) + "
            f"Full-Rated Cu Neutral & Earth Bars"
        )
        return enc, dims, max_diss, ip, std_ref, busbar_a, bars

    busbar_a = 160 if busbar_req_a > 125 else (125 if busbar_req_a > 80 else 100)
    if mpr <= 18:
        dims_map = {
            2: ("420 x 380 x 120 mm", 62.0),
            3: ("420 x 520 x 120 mm", 86.0),
            4: ("420 x 660 x 120 mm", 112.0),
        }
        dims, max_diss = dims_map.get(n_rows, (f"420 x {160 + n_rows * 130} x 120 mm", 135.0))
    else:
        dims_map24 = {
            2: ("540 x 420 x 140 mm", 90.0),
            3: ("540 x 560 x 140 mm", 122.0),
            4: ("540 x 700 x 140 mm", 155.0),
            5: ("540 x 850 x 140 mm", 190.0),
        }
        dims, max_diss = dims_map24.get(n_rows, (f"540 x {160 + n_rows * 140} x 140 mm", 220.0))

    if is_commercial:
        ip = "IP40 / IK08"
        std_ref = "IEC 61439-3 Commercial Steel Distribution Board"
        enc = (
            f"Commercial Steel Flush/Surface Distribution Board (IEC 61439-3), "
            f"{n_rows}x{mpr}M ({cap}M total, {spare_pct:.0f}% spare), IP40"
        )
    else:
        ip = "IP40 / IK07"
        std_ref = "IEC 61439-3 / BS 7671 Reg 421.1.201"
        enc = (
            f"Non-Combustible Steel Enclosure (Reg 421.1.201), "
            f"{n_rows}x{mpr}M ({cap}M total, {spare_pct:.0f}% spare), IP40"
        )
    ways = max(12, cap // 2)
    bars = (
        f"{busbar_a}A Insulated 3P+N Fork/Pin Comb Busbars + "
        f"{ways}-Way Brass Neutral & Earth Terminal Bars"
    )
    return enc, dims, max_diss, ip, std_ref, busbar_a, bars


def _circuit_slot(c: SolvedCircuit) -> tuple[str, BoardModuleSlot]:
    w = 4 if c.spec.is_three_phase else 2
    ph = _phase_literal(c.phase)
    item = f"{c.spec.circuit_id} {c.breaker_in_a}A {c.breaker_curve.value} RCBO"
    slot = BoardModuleSlot(
        slot_start_m=1,
        width_m=w,
        kind="rcbo",
        code=c.spec.circuit_id,
        label=f"{c.breaker_in_a}A {c.breaker_curve.value}",
        phase=ph,
        circuit_id=c.spec.circuit_id,
        rating_a=c.breaker_in_a,
        power_loss_w=_device_power_loss_w("rcbo", c.breaker_in_a, c.spec.is_three_phase),
    )
    return item, slot


def _build_door_legend(
    rows: list[DinRailRow],
    circuits_by_id: dict[str, SolvedCircuit],
    extra_entries: dict[str, tuple[str, str, str]],
) -> list[BoardCircuitLegendRow]:
    """Generate BS 7671 Reg 514.9.1 Inside-Door Circuit Directory for every active way."""
    legend: list[BoardCircuitLegendRow] = []
    way = 1
    for row in rows:
        for sl in row.slots:
            if sl.kind == "spare":
                continue
            if sl.circuit_id and sl.circuit_id in circuits_by_id:
                c = circuits_by_id[sl.circuit_id]
                rcd_tag = f" / {c.spec.rcd_idn_ma}mA" if c.spec.rcd_protected else ""
                dev_spec = f"{c.breaker_in_a}A {c.breaker_curve.value} RCBO{rcd_tag}"
                cable_spec = f"{c.cable_mm2:g}/{c.cpc_mm2:g} mm² Cu"
                desc = c.spec.description
            elif sl.code in extra_entries:
                dev_spec, cable_spec, desc = extra_entries[sl.code]
            else:
                dev_spec = sl.label
                cable_spec = "Internal Busbar"
                desc = sl.code
            legend.append(
                BoardCircuitLegendRow(
                    way_no=way,
                    rail_no=row.row_no,
                    circuit_id=sl.circuit_id or sl.code,
                    phase=sl.phase,
                    device_spec=dev_spec,
                    cable_spec=cable_spec,
                    description=desc,
                )
            )
            way += 1
    return legend


def _meter_tails_spec(main_in_a: int, is_3p: bool) -> str:
    if main_in_a >= 160:
        return f"4x95mm² XLPE/SWA + 50mm² Cu PE (EAC CT Metering {main_in_a}A 3P)"
    if main_in_a >= 100:
        return f"4x35mm² XLPE/PVC Meter Tails + 16mm² Cu PE ({main_in_a}A 3P)"
    if main_in_a >= 80:
        return f"{'4x25mm²' if is_3p else '2x25mm²'} XLPE/PVC Meter Tails + 16mm² Cu PE"
    return f"{'4x16mm²' if is_3p else '2x16mm²'} XLPE/PVC Meter Tails + 16mm² Cu PE"


def _finalize_board(
    *,
    board_id: str,
    floor: str,
    board_role: Literal[
        "main",
        "secondary_floor",
        "secondary_pool",
        "secondary_eps",
        "secondary_functional",
    ],
    parent_board_id: str | None,
    raw_slots: list[tuple[str, BoardModuleSlot]],
    circuits: list[SolvedCircuit],
    extra_legend: dict[str, tuple[str, str, str]],
    incoming_supply_spec: str,
    incoming_ipf_ka: float,
    min_device_icu_ka: float,
    selectivity_pass: bool,
    selectivity_note: str,
    busbar_req_a: int,
    is_panelboard_61439_2: bool = False,
    is_commercial: bool = False,
    is_outdoor_ip65: bool = False,
    modules_per_row: int = 18,
    min_rows: int = 2,
) -> DinRailBoardLayout:
    rows, used, spare, cap, loss_w = _pack_din_slots(
        raw_slots, modules_per_row=modules_per_row, min_rows=min_rows
    )
    mpr = rows[0].modules_capacity if rows else modules_per_row
    spare_pct = round(100.0 * spare / max(cap, 1), 1)
    enc, dims, max_diss, ip, std_ref, busbar_a, bars = _enclosure_catalog_spec(
        len(rows),
        mpr,
        spare_pct,
        busbar_req_a,
        is_panelboard_61439_2=is_panelboard_61439_2,
        is_commercial=is_commercial,
        is_outdoor_ip65=is_outdoor_ip65,
    )
    # Bump enclosure size by 1 spare rail if IEC 60890 thermal budget exceeded
    if loss_w > max_diss and not is_panelboard_61439_2:
        rows, used, spare, cap, loss_w = _pack_din_slots(
            raw_slots, modules_per_row=max(mpr, 24), min_rows=len(rows) + 1
        )
        mpr = rows[0].modules_capacity
        spare_pct = round(100.0 * spare / max(cap, 1), 1)
        enc, dims, max_diss, ip, std_ref, busbar_a, bars = _enclosure_catalog_spec(
            len(rows),
            mpr,
            spare_pct,
            busbar_req_a,
            is_panelboard_61439_2=is_panelboard_61439_2,
            is_commercial=is_commercial,
            is_outdoor_ip65=is_outdoor_ip65,
        )

    if board_role == "secondary_eps":
        std_ref = f"{std_ref} / EAC 2026 Storage EPS"
    c_by_id = {c.spec.circuit_id: c for c in circuits}
    door_legend = _build_door_legend(rows, c_by_id, extra_legend)
    return DinRailBoardLayout(
        board_id=board_id,
        floor=floor,
        board_role=board_role,
        parent_board_id=parent_board_id,
        standard_ref=std_ref,
        ip_rating=ip,
        busbar_rating_a=busbar_a,
        incoming_supply_spec=incoming_supply_spec,
        enclosure_dims_mm=dims,
        terminal_bars_spec=bars,
        power_dissipation_w=loss_w,
        max_enclosure_dissipation_w=max_diss,
        thermal_pass=loss_w <= max_diss + 1e-9,
        phase_loads_a=_board_phase_loads_a(circuits),
        total_modules_used=used,
        spare_modules=spare,
        total_modules_capacity=cap,
        spare_pct=spare_pct,
        enclosure_spec=enc,
        rows=rows,
        door_legend=door_legend,
        incoming_ipf_ka=round(incoming_ipf_ka, 2),
        min_device_icu_ka=min_device_icu_ka,
        selectivity_pass=selectivity_pass,
        selectivity_note=selectivity_note,
    )


def is_spd_type12_required(design: ElectricalDesignResult) -> bool:
    """Return True when SPD Type 1+2 is mandatory (IEC 62305 LPS or EAC HMY 58.18-1 overhead)."""
    lps_req = bool(design.lightning_risk is not None and design.lightning_risk.lps_required)
    is_overhead = bool(
        design.eac_metadata is not None and design.eac_metadata.supply_connection_type == "overhead"
    )
    return lps_req or is_overhead


def compute_din_rail_layouts(
    design: ElectricalDesignResult,
) -> list[DinRailBoardLayout]:
    """Build complete IEC 61439 Main & Secondary Distribution Board designs + door schedules."""
    eac = design.eac_compliance
    is_3p = eac.supply_phases == 3
    brief = design.residential_brief
    ipf_origin = design.prospective_fault_current_ka
    min_icu = 10.0 if ipf_origin > 6.0 else 6.0
    is_commercial = design.building.building_type in _COMMERCIAL_BUILDING_TYPES
    main_in = eac.recommended_main_breaker_a
    spd_t12 = is_spd_type12_required(design)
    spd_txt = "SPD TYPE 1+2 50kA" if spd_t12 else "SPD TYPE 2 40kA"
    spd_lbl = "T1+2 50kA" if spd_t12 else "T2 40kA"

    layouts: list[DinRailBoardLayout] = []
    has_sub_boards = len(design.building.sub_boards) > 0
    incomer_ph: _SlotPhase = "3P" if is_3p else "L1"
    poles_lbl = "4P" if is_3p else "2P"
    w_main = 4 if is_3p else 2

    if has_sub_boards:
        # 1. Main Distribution Board (MDB) feeding all floor sub-mains
        is_pb = is_commercial and main_in > 100
        mdb_slots: list[tuple[str, BoardModuleSlot]] = []
        mdb_extra: dict[str, tuple[str, str, str]] = {}
        inc_w = 8 if is_pb else w_main
        inc_kind_txt = "MAIN MCCB" if is_pb else "MAIN ISOLATOR"
        mdb_slots.append(
            (
                f"{inc_kind_txt} {main_in}A {poles_lbl}",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=inc_w,
                    kind="incomer",
                    code="Q0",
                    label=f"{main_in}A {poles_lbl}",
                    phase=incomer_ph,
                    rating_a=main_in,
                    power_loss_w=_device_power_loss_w("incomer", main_in, is_3p),
                ),
            )
        )
        mdb_extra["Q0"] = (
            f"{main_in}A {poles_lbl} {'MCCB' if is_pb else 'Main Isolator'}",
            _meter_tails_spec(main_in, is_3p),
            "Main Incoming Supply Isolator from EAC Meter",
        )
        if is_pb:
            mdb_slots.append(
                (
                    "CT METERING & MULTIMETER",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=4,
                        kind="meter",
                        code="M1",
                        label="CT METER",
                        phase="3P",
                        power_loss_w=_device_power_loss_w("meter", None, True),
                    ),
                )
            )
            mdb_extra["M1"] = (
                "EAC CT Metering / kWh Analyzer",
                "3x CT Secondary 2.5mm²",
                "Three-Phase CT Metering & Power Analyzer",
            )
        if design.include_tt_main_rcd_s and not is_pb:
            mdb_slots.append(
                (
                    "MAIN RCD 100mA TYPE-S",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=w_main,
                        kind="rcd_s",
                        code="F0",
                        label="100mA-S",
                        phase=incomer_ph,
                        rating_a=main_in,
                        power_loss_w=_device_power_loss_w("rcd_s", main_in, is_3p),
                    ),
                )
            )
            mdb_extra["F0"] = (
                f"{main_in}A {poles_lbl} 100mA Type-S RCCB",
                "Internal Busbar",
                "Upstream Time-Delayed Selective RCD (TT Earthing)",
            )
        mdb_slots.append(
            (
                spd_txt,
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=w_main,
                    kind="spd",
                    code="SPD",
                    label=spd_lbl,
                    phase=incomer_ph,
                    power_loss_w=_device_power_loss_w("spd", None, is_3p),
                ),
            )
        )
        mdb_extra["SPD"] = (
            spd_txt,
            "16mm² Cu PE (<0.5m)",
            "Transient Overvoltage Surge Protection (Reg 443)",
        )
        if design.building.pv_capacity_kva > 0:
            mdb_slots.append(
                (
                    "PV AC RCBO/ISOL",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=w_main,
                        kind="pv_isol",
                        code="Q-PV",
                        label=f"PV {design.building.pv_capacity_kva:g}kVA",
                        phase=incomer_ph,
                        rating_a=32,
                        power_loss_w=_device_power_loss_w("pv_isol", 32, is_3p),
                    ),
                )
            )
            mdb_extra["Q-PV"] = (
                f"Lockable AC Isolator + Bidirectional RCBO "
                f"({design.building.pv_capacity_kva:g} kVA)",
                "6/6 mm² Cu",
                "Grid-Tie PV Inverter Bidirectional Feeder (EAC 2026)",
            )
        for sm in design.sub_mains:
            fcode = f"Q-{sm.board_id}"
            fw = 4 if sm.is_three_phase else 2
            mdb_slots.append(
                (
                    f"FEEDER {sm.board_id} {sm.breaker_in_a}A {sm.breaker_curve.value}",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=fw,
                        kind="feeder",
                        code=fcode,
                        label=f"{sm.board_id} {sm.breaker_in_a}A",
                        phase="3P" if sm.is_three_phase else "L1",
                        rating_a=sm.breaker_in_a,
                        power_loss_w=_device_power_loss_w(
                            "feeder", sm.breaker_in_a, sm.is_three_phase
                        ),
                    ),
                )
            )
            mdb_extra[fcode] = (
                sm.device_label,
                f"{sm.cable_mm2:g}/{sm.cpc_mm2:g} mm² Cu ({sm.length_m:g}m)",
                f"Sub-Main Feeder to Secondary Board {sm.board_id} ({sm.floor})",
            )
        mdb_direct = [c for c in design.circuits if c.spec.board_id == design.building.db_anchor.id]
        for c in mdb_direct:
            mdb_slots.append(_circuit_slot(c))

        max_sm_in = max((sm.breaker_in_a for sm in design.sub_mains), default=40)
        sel_ratio = main_in / max(max_sm_in, 1)
        sel_pass = sel_ratio >= 1.25 or design.include_tt_main_rcd_s
        sel_note = (
            f"Upstream {main_in}A + 100mA Type-S (Δt>=40ms) -> "
            f"Sub-mains <= {max_sm_in}A -> Final 30mA RCBOs: "
            f"{'PASS (Time+Current Selective)' if sel_pass else 'VERIFY DISCRIMINATION'}"
        )
        layouts.append(
            _finalize_board(
                board_id=design.building.db_anchor.id,
                floor="GF / MAIN",
                board_role="main",
                parent_board_id=None,
                raw_slots=mdb_slots,
                circuits=design.circuits,
                extra_legend=mdb_extra,
                incoming_supply_spec=_meter_tails_spec(main_in, is_3p),
                incoming_ipf_ka=ipf_origin,
                min_device_icu_ka=min_icu,
                selectivity_pass=sel_pass,
                selectivity_note=sel_note,
                busbar_req_a=main_in,
                is_panelboard_61439_2=is_pb,
                is_commercial=is_commercial,
            )
        )

        # 2. Each Floor Secondary Distribution Board (DB-GF, DB-1F, ...)
        sm_by_board = {sm.board_id: sm for sm in design.sub_mains}
        for sb in design.building.sub_boards:
            sub_m = sm_by_board.get(sb.board_id)
            sb_in = sub_m.breaker_in_a if sub_m else 63
            sb_poles = "4P" if sb.is_three_phase else "2P"
            sb_w = 4 if sb.is_three_phase else 2
            sb_slots: list[tuple[str, BoardModuleSlot]] = [
                (
                    f"LOCAL ISOLATOR {sb_in}A {sb_poles}",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=sb_w,
                        kind="incomer",
                        code="Q0",
                        label=f"{sb_in}A {sb_poles}",
                        phase="3P" if sb.is_three_phase else "L1",
                        rating_a=sb_in,
                        power_loss_w=_device_power_loss_w("incomer", sb_in, sb.is_three_phase),
                    ),
                )
            ]
            sub_supply = (
                f"{sub_m.cable_mm2:g}/{sub_m.cpc_mm2:g}mm² Cu Sub-Main from "
                f"{sub_m.parent_board_id} ({sub_m.breaker_in_a}A {sub_m.breaker_curve.value}, "
                f"{sub_m.length_m:g}m)"
                if sub_m
                else f"Sub-Main Feeder from {sb.parent_board_id}"
            )
            sb_extra: dict[str, tuple[str, str, str]] = {
                "Q0": (
                    f"{sb_in}A {sb_poles} Local Main Isolator",
                    sub_supply,
                    f"Local Disconnection for Secondary Board {sb.board_id} ({sb.floor})",
                )
            }
            board_circs = [c for c in design.circuits if c.spec.board_id == sb.board_id]
            for c in board_circs:
                sb_slots.append(_circuit_slot(c))
            if brief.include_knx and sb.floor in ("GF", design.building.sub_boards[0].floor):
                for k_code, k_title, k_lbl, k_desc in (
                    ("KNX-P", "KNX PSU 640mA", "KNX PSU", "KNX 640mA Bus Power Supply + Choke"),
                    (
                        "KNX-S",
                        "KNX 8-CH SWITCH ACTUATOR",
                        "KNX 8CH",
                        "KNX 8-Channel Lighting Relay Actuator",
                    ),
                    (
                        "KNX-D",
                        "KNX 4-CH SHUTTER/DIM ACT",
                        "KNX DIM",
                        "KNX 4-Channel Shutter & DALI Dimming Actuator",
                    ),
                ):
                    sb_slots.append(
                        (
                            k_title,
                            BoardModuleSlot(
                                slot_start_m=1,
                                width_m=4,
                                kind="knx",
                                code=k_code,
                                label=k_lbl,
                                phase="L1",
                                power_loss_w=_device_power_loss_w("knx", None, False),
                            ),
                        )
                    )
                    sb_extra[k_code] = ("4M DIN KNX Module", "KNX EIB Bus 2x2x0.8mm", k_desc)

            sb_ipf = (
                sub_m.downstream_ipf_ka
                if (sub_m and sub_m.downstream_ipf_ka)
                else max(1.5, ipf_origin * 0.7)
            )
            sb_icu = 10.0 if sb_ipf > 6.0 else 6.0
            max_fin = max((c.breaker_in_a for c in board_circs), default=32)
            up_in = sub_m.breaker_in_a if sub_m else main_in
            sel_ok = up_in >= max_fin
            layouts.append(
                _finalize_board(
                    board_id=sb.board_id,
                    floor=sb.floor,
                    board_role="secondary_floor",
                    parent_board_id=sb.parent_board_id,
                    raw_slots=sb_slots,
                    circuits=board_circs,
                    extra_legend=sb_extra,
                    incoming_supply_spec=sub_supply,
                    incoming_ipf_ka=sb_ipf,
                    min_device_icu_ka=sb_icu,
                    selectivity_pass=sel_ok,
                    selectivity_note=(
                        f"Sub-main {up_in}A -> Max Final {max_fin}A 30mA RCBO "
                        f"(Ipf={sb_ipf:.2f} kA <= Icu={sb_icu:.0f} kA)"
                    ),
                    busbar_req_a=sb_in,
                    is_commercial=is_commercial,
                )
            )
    elif is_commercial and main_in > 100:
        # High-Current Commercial (>100A CT-Metered): IEC 61439-2 Form 2b MCCB Panelboard
        # feeding functional Secondary Boards (SDB-LP Lighting/Power & SDB-HVAC 3P Mechanical)
        hvac_circs = [
            c
            for c in design.circuits
            if c.spec.category == CircuitCategory.HVAC_3P
            or (c.spec.is_three_phase and c.breaker_in_a >= 32)
        ]
        hvac_ids = {c.spec.circuit_id for c in hvac_circs}
        lp_circs = [c for c in design.circuits if c.spec.circuit_id not in hvac_ids]

        lp_ib = max(_board_phase_loads_a(lp_circs).values(), default=40.0)
        lp_feeder_a = next((r for r in (63, 80, 100, 125, 160) if r >= lp_ib * 1.15), 160)
        hv_ib = max(_board_phase_loads_a(hvac_circs).values(), default=0.0)
        hv_feeder_a = next((r for r in (63, 80, 100, 125, 160, 200) if r >= hv_ib * 1.15), 200)

        mdb_slots = [
            (
                f"MAIN MCCB {main_in}A 4P",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=8,
                    kind="incomer",
                    code="Q0",
                    label=f"MCCB {main_in}A",
                    phase="3P",
                    rating_a=main_in,
                    power_loss_w=_device_power_loss_w("incomer", main_in, True),
                ),
            ),
            (
                "EAC CT METERING & MULTIMETER",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=4,
                    kind="meter",
                    code="M1",
                    label="CT METER",
                    phase="3P",
                    power_loss_w=_device_power_loss_w("meter", None, True),
                ),
            ),
            (
                "SPD TYPE 1+2 50kA",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=4,
                    kind="spd",
                    code="SPD",
                    label="T1+2 50kA",
                    phase="3P",
                    power_loss_w=_device_power_loss_w("spd", None, True),
                ),
            ),
        ]
        mdb_extra = {
            "Q0": (
                f"{main_in}A 4P MCCB (Icu 25kA)",
                _meter_tails_spec(main_in, True),
                "Main Incoming MCCB (IEC 61439-2 Form 2b Panelboard)",
            ),
            "M1": (
                "EAC CT Metering / kWh Power Analyzer",
                "3x 200/5A CT Secondary 2.5mm²",
                "EAC Approved CT Metering & Phase Ammeter/Voltmeter",
            ),
            "SPD": (
                "SPD Type 1+2 50kA (3P+N)",
                "16mm² Cu PE (<0.5m)",
                "Main Switchboard Surge Arrester (IEC 61643-11)",
            ),
        }
        if design.building.pv_capacity_kva > 0:
            mdb_slots.append(
                (
                    f"PV MCCB/ISOL {design.building.pv_capacity_kva:g}kVA",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=4,
                        kind="pv_isol",
                        code="Q-PV",
                        label=f"PV {design.building.pv_capacity_kva:g}kVA",
                        phase="3P",
                        rating_a=40,
                        power_loss_w=_device_power_loss_w("pv_isol", 40, True),
                    ),
                )
            )
            mdb_extra["Q-PV"] = (
                "40A 4P Lockable Isolator + Bidirectional RCBO "
                f"({design.building.pv_capacity_kva:g} kVA)",
                "10/10 mm² XLPE/SWA",
                "Commercial Rooftop PV Bidirectional Inverter Feeder",
            )
        mdb_slots.append(
            (
                f"FEEDER SDB-LP {lp_feeder_a}A 4P MCCB",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=6 if lp_feeder_a > 63 else 4,
                    kind="feeder",
                    code="Q-LP",
                    label=f"SDB-LP {lp_feeder_a}A",
                    phase="3P",
                    rating_a=lp_feeder_a,
                    power_loss_w=_device_power_loss_w("feeder", lp_feeder_a, True),
                ),
            )
        )
        lp_cable = "35/16 mm²" if lp_feeder_a >= 100 else "25/16 mm²"
        mdb_extra["Q-LP"] = (
            f"{lp_feeder_a}A 4P MCCB + 100mA Type-S",
            f"{lp_cable} XLPE/SWA",
            "Sub-Main Feeder to Secondary Lighting, Small Power & IT Board (SDB-LP)",
        )
        if hvac_circs:
            mdb_slots.append(
                (
                    f"FEEDER SDB-HVAC {hv_feeder_a}A 4P MCCB",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=6 if hv_feeder_a > 63 else 4,
                        kind="feeder",
                        code="Q-HVAC",
                        label=f"HVAC {hv_feeder_a}A",
                        phase="3P",
                        rating_a=hv_feeder_a,
                        power_loss_w=_device_power_loss_w("feeder", hv_feeder_a, True),
                    ),
                )
            )
            hv_cable = "70/35 mm²" if hv_feeder_a >= 160 else "50/25 mm²"
            mdb_extra["Q-HVAC"] = (
                f"{hv_feeder_a}A 4P MCCB (Thermal-Magnetic)",
                f"{hv_cable} XLPE/SWA",
                "Sub-Main Feeder to Secondary 3-Phase HVAC/VRF Board (SDB-HVAC)",
            )
        layouts.append(
            _finalize_board(
                board_id=design.building.db_anchor.id,
                floor="GF / MAIN SWITCHBOARD",
                board_role="main",
                parent_board_id=None,
                raw_slots=mdb_slots,
                circuits=design.circuits,
                extra_legend=mdb_extra,
                incoming_supply_spec=_meter_tails_spec(main_in, True),
                incoming_ipf_ka=ipf_origin,
                min_device_icu_ka=max(10.0, min_icu),
                selectivity_pass=True,
                selectivity_note=(
                    f"IEC 61439-2 Form 2b {main_in}A MCCB -> SDB-LP ({lp_feeder_a}A)"
                    + (f" + SDB-HVAC ({hv_feeder_a}A)" if hvac_circs else "")
                    + f" (Ipf={ipf_origin:.1f} kA <= Icu=25 kA)"
                ),
                busbar_req_a=main_in,
                is_panelboard_61439_2=True,
                is_commercial=True,
                modules_per_row=24,
            )
        )

        # Secondary Board 1: SDB-LP (Lighting, Small Power, Fire/Emergency & IT UPS)
        lp_slots: list[tuple[str, BoardModuleSlot]] = [
            (
                f"LOCAL ISOLATOR {lp_feeder_a}A 4P",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=4,
                    kind="incomer",
                    code="Q0",
                    label=f"{lp_feeder_a}A 4P",
                    phase="3P",
                    rating_a=lp_feeder_a,
                    power_loss_w=_device_power_loss_w("incomer", lp_feeder_a, True),
                ),
            )
        ]
        lp_supply = (
            f"{lp_cable} XLPE/SWA Sub-Main from "
            f"{design.building.db_anchor.id} ({lp_feeder_a}A MCCB)"
        )
        lp_extra = {
            "Q0": (
                f"{lp_feeder_a}A 4P Load-Break Isolator",
                lp_supply,
                "Local Isolator for Commercial Lighting, Small Power & IT Board",
            )
        }
        for c in lp_circs:
            lp_slots.append(_circuit_slot(c))
        lp_ipf = round(max(2.0, ipf_origin * 0.80), 2)
        lp_icu = 10.0 if lp_ipf > 6.0 else 6.0
        layouts.append(
            _finalize_board(
                board_id="SDB-LP",
                floor="GF / LIGHTING & POWER",
                board_role="secondary_functional",
                parent_board_id=design.building.db_anchor.id,
                raw_slots=lp_slots,
                circuits=lp_circs,
                extra_legend=lp_extra,
                incoming_supply_spec=lp_supply,
                incoming_ipf_ka=lp_ipf,
                min_device_icu_ka=lp_icu,
                selectivity_pass=True,
                selectivity_note=(
                    f"{design.building.db_anchor.id} {lp_feeder_a}A MCCB -> Final 30mA RCBOs "
                    f"(Ipf={lp_ipf:.2f} kA <= Icu={lp_icu:.0f} kA)"
                ),
                busbar_req_a=lp_feeder_a,
                is_commercial=True,
            )
        )

        # Secondary Board 2: SDB-HVAC (3-Phase VRF / Mechanical Plant)
        if hvac_circs:
            hv_slots: list[tuple[str, BoardModuleSlot]] = [
                (
                    f"LOCAL ISOLATOR {hv_feeder_a}A 4P",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=6 if hv_feeder_a > 125 else 4,
                        kind="incomer",
                        code="Q0",
                        label=f"{hv_feeder_a}A 4P",
                        phase="3P",
                        rating_a=hv_feeder_a,
                        power_loss_w=_device_power_loss_w("incomer", hv_feeder_a, True),
                    ),
                )
            ]
            hv_supply = (
                f"{hv_cable} XLPE/SWA Sub-Main from "
                f"{design.building.db_anchor.id} ({hv_feeder_a}A MCCB)"
            )
            hv_extra = {
                "Q0": (
                    f"{hv_feeder_a}A 4P Load-Break Isolator",
                    hv_supply,
                    "Local Isolator for 3-Phase Mechanical / VRF HVAC Sub-Board",
                )
            }
            for c in hvac_circs:
                hv_slots.append(_circuit_slot(c))
            hv_ipf = round(max(2.5, ipf_origin * 0.85), 2)
            hv_icu = 10.0 if hv_ipf > 6.0 else 6.0
            layouts.append(
                _finalize_board(
                    board_id="SDB-HVAC",
                    floor="PLANT / 3P HVAC",
                    board_role="secondary_functional",
                    parent_board_id=design.building.db_anchor.id,
                    raw_slots=hv_slots,
                    circuits=hvac_circs,
                    extra_legend=hv_extra,
                    incoming_supply_spec=hv_supply,
                    incoming_ipf_ka=hv_ipf,
                    min_device_icu_ka=hv_icu,
                    selectivity_pass=True,
                    selectivity_note=(
                        f"{design.building.db_anchor.id} {hv_feeder_a}A MCCB -> 3P HVAC RCBOs "
                        f"(Ipf={hv_ipf:.2f} kA <= Icu={hv_icu:.0f} kA)"
                    ),
                    busbar_req_a=hv_feeder_a,
                    is_commercial=True,
                    modules_per_row=24 if hv_feeder_a >= 125 else 18,
                )
            )
    else:
        # Single-board building (DB1 / <=100A commercial MDB1)
        db_slots: list[tuple[str, BoardModuleSlot]] = [
            (
                f"MAIN ISOLATOR {main_in}A {poles_lbl}",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=w_main,
                    kind="incomer",
                    code="Q0",
                    label=f"{main_in}A {poles_lbl}",
                    phase=incomer_ph,
                    rating_a=main_in,
                    power_loss_w=_device_power_loss_w("incomer", main_in, is_3p),
                ),
            )
        ]
        db_extra: dict[str, tuple[str, str, str]] = {
            "Q0": (
                f"{main_in}A {poles_lbl} Main Isolator",
                _meter_tails_spec(main_in, is_3p),
                "Main Incoming Supply Isolator from EAC Meter",
            )
        }
        if design.include_tt_main_rcd_s:
            db_slots.append(
                (
                    "MAIN RCD 100mA TYPE-S",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=w_main,
                        kind="rcd_s",
                        code="F0",
                        label="100mA-S",
                        phase=incomer_ph,
                        rating_a=main_in,
                        power_loss_w=_device_power_loss_w("rcd_s", main_in, is_3p),
                    ),
                )
            )
            db_extra["F0"] = (
                f"{main_in}A {poles_lbl} 100mA Type-S RCCB",
                "Internal Busbar",
                "Upstream Time-Delayed Selective RCD (TT Earthing)",
            )
        db_slots.append(
            (
                spd_txt,
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=w_main,
                    kind="spd",
                    code="SPD",
                    label=spd_lbl,
                    phase=incomer_ph,
                    power_loss_w=_device_power_loss_w("spd", None, is_3p),
                ),
            )
        )
        db_extra["SPD"] = (
            spd_txt,
            "16mm² Cu PE (<0.5m)",
            "Transient Overvoltage Surge Protection (Reg 443)",
        )
        if design.building.pv_capacity_kva > 0:
            db_slots.append(
                (
                    "PV AC RCBO/ISOL",
                    BoardModuleSlot(
                        slot_start_m=1,
                        width_m=w_main,
                        kind="pv_isol",
                        code="Q-PV",
                        label=f"PV {design.building.pv_capacity_kva:g}kVA",
                        phase=incomer_ph,
                        rating_a=32,
                        power_loss_w=_device_power_loss_w("pv_isol", 32, is_3p),
                    ),
                )
            )
            db_extra["Q-PV"] = (
                "Lockable AC Isolator + Bidirectional RCBO "
                f"({design.building.pv_capacity_kva:g} kVA)",
                "6/6 mm² Cu",
                "Grid-Tie PV Inverter Bidirectional Feeder (EAC 2026)",
            )
        for c in design.circuits:
            db_slots.append(_circuit_slot(c))
        if brief.include_knx:
            for k_code, k_title, k_lbl, k_desc in (
                ("KNX-P", "KNX PSU 640mA", "KNX PSU", "KNX 640mA Bus Power Supply + Choke"),
                (
                    "KNX-S",
                    "KNX 8-CH SWITCH ACTUATOR",
                    "KNX 8CH",
                    "KNX 8-Channel Lighting Relay Actuator",
                ),
                (
                    "KNX-D",
                    "KNX 4-CH SHUTTER/DIM ACT",
                    "KNX DIM",
                    "KNX 4-Channel Shutter & DALI Dimming Actuator",
                ),
            ):
                db_slots.append(
                    (
                        k_title,
                        BoardModuleSlot(
                            slot_start_m=1,
                            width_m=4,
                            kind="knx",
                            code=k_code,
                            label=k_lbl,
                            phase="L1",
                            power_loss_w=_device_power_loss_w("knx", None, False),
                        ),
                    )
                )
                db_extra[k_code] = ("4M DIN KNX Module", "KNX EIB Bus 2x2x0.8mm", k_desc)

        max_fin = max((c.breaker_in_a for c in design.circuits), default=32)
        sel_ok = main_in >= max_fin
        layouts.append(
            _finalize_board(
                board_id=design.building.db_anchor.id,
                floor="GF",
                board_role="main",
                parent_board_id=None,
                raw_slots=db_slots,
                circuits=design.circuits,
                extra_legend=db_extra,
                incoming_supply_spec=_meter_tails_spec(main_in, is_3p),
                incoming_ipf_ka=ipf_origin,
                min_device_icu_ka=min_icu,
                selectivity_pass=sel_ok,
                selectivity_note=(
                    f"Main {main_in}A + 100mA Type-S -> "
                    f"Max Final {max_fin}A 30mA RCBO "
                    f"(Ipf={ipf_origin:.1f} kA <= Icu={min_icu:.0f} kA)"
                ),
                busbar_req_a=main_in,
                is_commercial=is_commercial,
            )
        )

    # 3. Dedicated Outdoor IP65 Pool Plant Secondary Board (DB-POOL) per BS 7671 Section 702
    pool_feeder = next(
        (c for c in design.circuits if c.spec.category == CircuitCategory.POOL), None
    )
    if brief.include_pool and pool_feeder is not None:
        pool_parent = pool_feeder.spec.board_id
        pool_3p = pool_feeder.spec.is_three_phase
        pool_in = max(25, pool_feeder.breaker_in_a)
        pool_slots: list[tuple[str, BoardModuleSlot]] = [
            (
                f"LOCAL ISOLATOR {pool_in}A {'4P' if pool_3p else '2P'}",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=4 if pool_3p else 2,
                    kind="incomer",
                    code="Q-P0",
                    label=f"{pool_in}A {'4P' if pool_3p else '2P'}",
                    phase="3P" if pool_3p else "L1",
                    rating_a=pool_in,
                    power_loss_w=_device_power_loss_w("incomer", pool_in, pool_3p),
                ),
            ),
            (
                "P1 POOL FILTRATION PUMP 16A C RCBO",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=4 if pool_3p else 2,
                    kind="rcbo",
                    code="P1",
                    label="16A C",
                    phase="3P" if pool_3p else "L1",
                    rating_a=16,
                    power_loss_w=_device_power_loss_w("rcbo", 16, pool_3p),
                ),
            ),
            (
                "P2 UNDERWATER SELV 12V LED 6A B RCBO",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=2,
                    kind="rcbo",
                    code="P2",
                    label="6A B",
                    phase="L1",
                    rating_a=6,
                    power_loss_w=_device_power_loss_w("rcbo", 6, False),
                ),
            ),
            (
                "P3 SALT CHLORINATOR / DOSING 10A B RCBO",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=2,
                    kind="rcbo",
                    code="P3",
                    label="10A B",
                    phase="L2" if pool_3p else "L1",
                    rating_a=10,
                    power_loss_w=_device_power_loss_w("rcbo", 10, False),
                ),
            ),
            (
                "P4 PLANT ROOM IP65 SOCKET 16A B RCBO",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=2,
                    kind="rcbo",
                    code="P4",
                    label="16A B",
                    phase="L3" if pool_3p else "L1",
                    rating_a=16,
                    power_loss_w=_device_power_loss_w("rcbo", 16, False),
                ),
            ),
        ]
        pool_supply = (
            f"{pool_feeder.cable_mm2:g}/{pool_feeder.cpc_mm2:g}mm² XLPE/SWA from "
            f"{pool_parent} ({pool_feeder.spec.circuit_id} {pool_feeder.breaker_in_a}A "
            f"{pool_feeder.breaker_curve.value}, {pool_feeder.spec.length_m:g}m)"
        )
        pool_extra = {
            "Q-P0": (
                f"{pool_in}A {'4P' if pool_3p else '2P'} Local Isolator",
                pool_supply,
                "Pool Plant Room Main Disconnection (IP65 Lockable)",
            ),
            "P1": (
                f"16A {'3P' if pool_3p else '1P'} Type C 30mA RCBO",
                "2.5/2.5 mm² Cu",
                "Pool Circulation & Filtration Pump Motor (Zone 2/Plant)",
            ),
            "P2": (
                "6A 1P Type B 30mA RCBO + SELV 12V AC Tx",
                "1.5/1.5 mm² Cu",
                "Underwater Pool LED Lighting (BS 7671 Sec. 702 Zone 0 SELV)",
            ),
            "P3": (
                "10A 1P Type B 30mA RCBO",
                "1.5/1.5 mm² Cu",
                "Salt Electrolysis Chlorinator & Automatic pH Dosing",
            ),
            "P4": (
                "16A 1P Type B 30mA RCBO",
                "2.5/2.5 mm² Cu",
                "Pool Plant Room IP65 Maintenance Socket (>3.5m Zone 2)",
            ),
        }
        pool_ipf = round(
            (pool_feeder.ik1_min_a / 1000.0)
            if pool_feeder.ik1_min_a
            else max(0.8, ipf_origin * 0.45),
            2,
        )
        layouts.append(
            _finalize_board(
                board_id="DB-POOL",
                floor="SITE / POOL PLANT",
                board_role="secondary_pool",
                parent_board_id=pool_parent,
                raw_slots=pool_slots,
                circuits=[pool_feeder],
                extra_legend=pool_extra,
                incoming_supply_spec=pool_supply,
                incoming_ipf_ka=max(0.5, pool_ipf),
                min_device_icu_ka=6.0,
                selectivity_pass=True,
                selectivity_note=(
                    f"Feeder {pool_feeder.spec.circuit_id} ({pool_feeder.breaker_in_a}A) -> "
                    f"Local 30mA RCBOs + Sec. 702 Bonding (Ipf={max(0.5, pool_ipf):.2f} kA <= 6 kA)"
                ),
                busbar_req_a=63,
                is_outdoor_ip65=True,
                modules_per_row=12,
                min_rows=2,
            )
        )

    # 4. Dedicated Essential / EPS Backup Secondary Board (DB-EPS) per EAC 2026 Storage Guide
    if brief.include_eps_backup and brief.battery_storage_kwh > 0:
        eps_cats = frozenset(
            {
                CircuitCategory.ELV_POWER,
                CircuitCategory.KNX_BUS,
                CircuitCategory.UPS_SERVER,
                CircuitCategory.SMOKE_DETECTION,
                CircuitCategory.WATER_PUMP,
                CircuitCategory.FIRE_ALARM,
                CircuitCategory.EMERGENCY_LIGHTING,
            }
        )
        eps_circs: list[SolvedCircuit] = [
            c for c in design.circuits if c.spec.category in eps_cats
        ]
        # Add primary day lighting + primary kitchen/fridge socket circuit if present
        first_light = next(
            (c for c in design.circuits if c.spec.category == CircuitCategory.LIGHTING), None
        )
        if first_light is not None and first_light not in eps_circs:
            eps_circs.insert(0, first_light)
        first_ring = next(
            (c for c in design.circuits if c.spec.category == CircuitCategory.SOCKET_RING), None
        )
        if first_ring is not None and first_ring not in eps_circs:
            eps_circs.append(first_ring)

        eps_in = 40
        eps_slots: list[tuple[str, BoardModuleSlot]] = [
            (
                f"EPS ATS {eps_in}A + N-E ISLANDING RELAY",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=4,
                    kind="ats",
                    code="ATS",
                    label=f"ATS {eps_in}A",
                    phase=incomer_ph,
                    rating_a=eps_in,
                    power_loss_w=_device_power_loss_w("ats", eps_in, is_3p),
                ),
            ),
            (
                f"EPS LOCAL ISOLATOR {eps_in}A {poles_lbl}",
                BoardModuleSlot(
                    slot_start_m=1,
                    width_m=w_main,
                    kind="incomer",
                    code="Q-E0",
                    label=f"{eps_in}A {poles_lbl}",
                    phase=incomer_ph,
                    rating_a=eps_in,
                    power_loss_w=_device_power_loss_w("incomer", eps_in, is_3p),
                ),
            ),
        ]
        eps_supply = (
            f"Hybrid Inverter EPS Port ({brief.battery_storage_kwh:g} kWh LFP) -> "
            f"40A ATS + Islanding N-E Bonding Contactor (EAC 2026 Storage Guide)"
        )
        eps_extra = {
            "ATS": (
                f"{eps_in}A Automatic Transfer Switch + N-E Contactor",
                "10/10 mm² Cu from Hybrid Inverter EPS",
                "EAC 2026 Anti-Islanding EPS Changeover & Neutral-Earth Interlock",
            ),
            "Q-E0": (
                f"{eps_in}A {poles_lbl} EPS Local Isolator",
                "Internal Busbar",
                "Essential Backup Loads Local Disconnection Switch",
            ),
        }
        for c in eps_circs[:8]:
            eps_slots.append(_circuit_slot(c))
        eps_ipf = round(max(1.2, ipf_origin * 0.65), 2)
        layouts.append(
            _finalize_board(
                board_id="DB-EPS",
                floor="GF / EPS BACKUP",
                board_role="secondary_eps",
                parent_board_id=design.building.db_anchor.id,
                raw_slots=eps_slots,
                circuits=eps_circs[:8],
                extra_legend=eps_extra,
                incoming_supply_spec=eps_supply,
                incoming_ipf_ka=eps_ipf,
                min_device_icu_ka=6.0,
                selectivity_pass=True,
                selectivity_note=(
                    f"Hybrid Inverter EPS + 40A ATS -> Critical 30mA RCBOs "
                    f"(Grid-mode Ipf={eps_ipf:.2f} kA <= 6 kA; Island-mode N-E Bonded)"
                ),
                busbar_req_a=63,
                is_commercial=is_commercial,
            )
        )

    return layouts


def compute_lightning_risk_iec62305(
    building: BuildingModel,
    brief: ResidentialSystemsBrief,
    *,
    is_overhead_supply: bool = False,
) -> tuple[LightningRiskAssessment, list[ReviewFlag]]:
    """Compute simplified IEC 62305-2 residential lightning risk (R1 vs RT = 1e-5)."""
    all_pts = [p for r in building.rooms for p in r.polygon_mm]
    if all_pts:
        span_x = max(5.0, (max(p.x_mm for p in all_pts) - min(p.x_mm for p in all_pts)) / 1000.0)
        span_y = max(5.0, (max(p.y_mm for p in all_pts) - min(p.y_mm for p in all_pts)) / 1000.0)
        # For side-by-side multi-floor plans, estimate footprint from primary floor
        if building.sub_boards:
            gf_pts = [
                p
                for r in building.rooms
                if r.floor == building.sub_boards[0].floor
                for p in r.polygon_mm
            ]
            if gf_pts:
                span_x = max(
                    5.0, (max(p.x_mm for p in gf_pts) - min(p.x_mm for p in gf_pts)) / 1000.0
                )
                span_y = max(
                    5.0, (max(p.y_mm for p in gf_pts) - min(p.y_mm for p in gf_pts)) / 1000.0
                )
    else:
        span_x, span_y = 12.0, 10.0

    h = brief.building_height_m
    ng = brief.lightning_flash_density_ng
    # IEC 62305-2 Annex A: Ad = L*W + 6*H*(L+W) + 9*pi*H^2
    ad_m2 = round(span_x * span_y + 6.0 * h * (span_x + span_y) + 9.0 * math.pi * h * h, 1)
    # Location factor Cd = 0.5 (suburban surrounded by similar structures; 1.0 isolated)
    cd = 1.0 if h >= 10.0 else 0.5
    nd = ng * ad_m2 * cd * 1e-6
    # Typical residential loss/probability factors (PB=1.0 no LPS, LF=0.01 ordinary fire risk,
    # hz=1.0, rp=1.0, rf=0.001 brick/concrete structure -> LB = 1e-3 * 0.01 * 0.5 = 5e-6?
    # Conservative residential physical damage factor LA+LB ~ 5e-3 for masonry house with PV/roof)
    p_loss = 0.0045 if building.pv_capacity_kva > 0 else 0.0030
    r1 = nd * p_loss
    rt = 1e-5
    lps_req = r1 > rt
    lps_class = (
        "Class IV External LPS Required" if lps_req else "External LPS Not Required (R1 <= RT)"
    )
    if lps_req or building.pv_capacity_kva > 0:
        spd_req = "Type 1+2 Combined SPD (12.5kA 10/350µs) at MDB + DC SPD for Roof PV"
    elif is_overhead_supply:
        spd_req = "Type 1+2 Combined SPD (12.5kA 10/350µs) at MDB (EAC HMY 58.18-1 Overhead Feed)"
    else:
        spd_req = "Type 2 SPD (40kA 8/20µs) at MDB (BS 7671 Reg 443.4)"

    assessment = LightningRiskAssessment(
        ng_flashes_km2_yr=ng,
        collection_area_ad_m2=ad_m2,
        nd_events_per_yr=round(nd, 6),
        tolerable_rt=rt,
        risk_r1=round(r1, 8),
        lps_required=lps_req,
        recommended_lps_class=lps_class,
        spd_requirement=spd_req,
    )
    flags: list[ReviewFlag] = []
    if lps_req:
        flags.append(
            ReviewFlag(
                code="IEC62305_EXTERNAL_LPS_REQUIRED",
                severity=ReviewSeverity.WARNING,
                category="eac",
                regulation_ref="IEC 62305-2 / CYS EN 62305-2",
                message=(
                    f"IEC 62305-2 Lightning Risk R1={r1:.2e} exceeds tolerable RT=1.0e-5 "
                    f"(Ng={ng}, H={h:.1f}m, Ad={ad_m2:.0f} m²). Design Class IV external LPS "
                    "with Type 1+2 SPD at MDB."
                ),
                requires_etek_signoff=True,
            )
        )
    return assessment, flags


def compute_pv_and_storage_engineering(
    pv_capacity_kva: float,
    supply_phases: Literal[1, 3],
    brief: ResidentialSystemsBrief,
) -> tuple[PvStorageEngineeringResult | None, list[ReviewFlag]]:
    """Deterministic DC-side PV stringing & Battery Storage (BESS) sizing for Cyprus (EAC 2026).

    Verifies Cyprus winter open-circuit voltage (T_min = 0°C vs inverter Vdc_max),
    summer rooftop MPPT tracking window (T_cell = 70°C), EN 50618 H1Z2Z2-K DC solar
    cable voltage drop (<=1.5%), DC Type 1+2 SPD & DC load-break isolator ratings,
    and optional LiFePO4 battery storage DC protection & EPS backup changeover per
    March 2026 EAC Technical Guide DRG. No PVS-S-1.2.
    """
    if pv_capacity_kva <= 0.0:
        return None, []

    inv_phases: Literal[1, 3] = 3 if (supply_phases == 3 and pv_capacity_kva > 4.2) else 1
    if inv_phases == 3:
        inv_max_dc_v = 1000
        mppt_min_v = 180
        mppt_max_v = 850
    else:
        inv_max_dc_v = 600
        mppt_min_v = 120
        mppt_max_v = 550

    wp = brief.pv_module_wp
    # N-Type monocrystalline reference scaled to selected Wp (550 Wp -> Voc=49.8V, Vmp=41.8V)
    scale_v = (wp / 550.0) ** 0.25
    voc_stc_mod = 49.8 * scale_v
    vmp_stc_mod = 41.8 * scale_v
    imp_stc_mod = wp / vmp_stc_mod
    isc_stc_mod = imp_stc_mod * 1.064

    # Cyprus thermal extremes: T_min = 0°C (winter morning), T_cell_hot = 70°C (45°C summer roof)
    voc_cold_mod = voc_stc_mod * (1.0 + (-0.0026) * (0.0 - 25.0))
    vmp_hot_mod = vmp_stc_mod * (1.0 + (-0.0034) * (70.0 - 25.0))

    target_dc_kwp = pv_capacity_kva * 1.10
    raw_modules = max(4, int(round((target_dc_kwp * 1000.0) / float(wp))))
    # Enforce >=6% voltage safety margin below inverter Vdc_max at 0°C
    max_ns = max(4, int(math.floor((inv_max_dc_v * 0.94) / voc_cold_mod)))
    min_ns = max(4, int(math.ceil((mppt_min_v * 1.12) / vmp_hot_mod)))

    if raw_modules <= max_ns and raw_modules <= 12:
        strings_count = 1
        modules_per_string = max(min_ns, raw_modules)
    else:
        strings_count = max(2, int(math.ceil(raw_modules / float(max_ns))))
        modules_per_string = max(min_ns, int(math.ceil(raw_modules / float(strings_count))))
        if modules_per_string > max_ns:
            strings_count += 1
            modules_per_string = max(min_ns, int(math.ceil(raw_modules / float(strings_count))))

    module_count = strings_count * modules_per_string
    array_dc_kwp = round((module_count * wp) / 1000.0, 2)
    dc_ac_ratio = round(array_dc_kwp / pv_capacity_kva, 2)

    string_voc_stc_v = round(modules_per_string * voc_stc_mod, 1)
    string_voc_cold_0c_v = round(modules_per_string * voc_cold_mod, 1)
    string_vmp_stc_v = round(modules_per_string * vmp_stc_mod, 1)
    string_vmp_hot_70c_v = round(modules_per_string * vmp_hot_mod, 1)
    # BS 7671 Reg 712.433 / IEC 62548: Isc_max = 1.25 * Isc_stc per string
    string_isc_max_a = round(isc_stc_mod * 1.25, 1)

    # EN 50618 H1Z2Z2-K double-insulated DC solar cable sizing (target <=1.5% DC voltage drop)
    dc_run_length_m = round(max(12.0, brief.building_height_m * 1.6 + 8.0), 1)
    rho_cu_70c = 0.0214  # Ohm*mm2/m at 70C conductor operating temp
    dc_cable_mm2 = 4.0
    dc_vd_pct = 0.0
    for candidate_mm2 in (4.0, 6.0, 10.0):
        dv_v = (2.0 * dc_run_length_m * imp_stc_mod * rho_cu_70c) / candidate_mm2
        pct = (dv_v / max(string_vmp_stc_v, 1.0)) * 100.0
        dc_cable_mm2 = candidate_mm2
        dc_vd_pct = round(pct, 2)
        if pct <= 1.5:
            break

    iso_rating_a = 25 if string_isc_max_a <= 20.0 else (32 if string_isc_max_a <= 28.0 else 40)
    dc_isolator_spec = (
        f"{strings_count}× 2P DC Load-Break Isolator {iso_rating_a}A "
        f"{inv_max_dc_v}V DC (IEC 60947-3, DC-PV2)"
    )
    spd_up_kv = "3.8kV" if inv_max_dc_v >= 1000 else "2.5kV"
    dc_spd_spec = (
        f"Type 1+2 DC SPD {inv_max_dc_v}V DC (Up≤{spd_up_kv}, EN 50539-11) per MPPT"
    )
    dc_cable_spec = (
        f"{strings_count}× (2×{dc_cable_mm2:g}mm² EN 50618 H1Z2Z2-K UV-Resistant Solar DC)"
    )

    voc_cold_pass = string_voc_cold_0c_v <= float(inv_max_dc_v)
    mppt_window_pass = float(mppt_min_v) <= string_vmp_hot_70c_v <= float(mppt_max_v)
    dc_vd_pass = dc_vd_pct <= 1.5

    # Optional Battery Energy Storage System (BESS) per March 2026 EAC DRG. No PVS-S-1.2
    batt_kwh = round(brief.battery_storage_kwh, 2)
    battery_enabled = batt_kwh > 0.0
    battery_usable_kwh = 0.0
    battery_bus_v = 0
    battery_max_kw = 0.0
    battery_max_i_a = 0.0
    battery_dc_breaker_spec = ""
    battery_dc_cable_spec = ""
    eps_enabled = bool(brief.include_eps_backup and battery_enabled)
    eps_spec = ""

    if battery_enabled:
        battery_usable_kwh = round(batt_kwh * 0.90, 2)
        # 3-phase or >10 kWh uses HV 384V LFP stack; small 1P systems use 51.2V LV LFP rack
        is_hv = inv_phases == 3 or batt_kwh > 10.0
        battery_bus_v = 384 if is_hv else 51
        v_min_discharge = 340.0 if is_hv else 48.0
        battery_max_kw = round(min(pv_capacity_kva, max(2.5, batt_kwh * 0.5)), 2)
        battery_max_i_a = round((battery_max_kw * 1000.0) / v_min_discharge, 1)
        req_bkr = battery_max_i_a * 1.25
        std_dc_ratings = (25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250)
        dc_bkr_a = next((r for r in std_dc_ratings if r >= req_bkr), 250)
        if is_hv:
            batt_mm2 = 6.0 if dc_bkr_a <= 32 else (10.0 if dc_bkr_a <= 50 else 16.0)
            battery_dc_breaker_spec = (
                f"2P DC MCCB / NH00 gG Fuse {dc_bkr_a}A 500V DC (Icu≥10kA DC)"
            )
            battery_dc_cable_spec = f"2×{batt_mm2:g}mm² HV DC Battery Cable (Double Insulated)"
        else:
            batt_mm2 = (
                25.0
                if dc_bkr_a <= 80
                else (35.0 if dc_bkr_a <= 125 else (50.0 if dc_bkr_a <= 160 else 70.0))
            )
            battery_dc_breaker_spec = (
                f"2P DC MCCB / NH00 gG Fuse {dc_bkr_a}A 125V DC (Icu≥20kA DC)"
            )
            battery_dc_cable_spec = (
                f"2×{batt_mm2:g}mm² Flexible Battery DC Cable (H07V-K / Class 5)"
            )

        if eps_enabled:
            poles = "4P" if inv_phases == 3 else "2P"
            eps_spec = (
                f"{poles} Motorized Automatic Transfer Switch (ATS / Break-Before-Make) + "
                "Critical-Load Sub-Busbar + Dedicated Islanding TT Earth Reference "
                "(BS 7671 Sec. 551 / EAC PVS-S-1.2 Annex VI)"
            )

    res = PvStorageEngineeringResult(
        inverter_ac_kva=round(pv_capacity_kva, 2),
        inverter_phases=inv_phases,
        inverter_max_dc_v=inv_max_dc_v,
        mppt_min_v=mppt_min_v,
        mppt_max_v=mppt_max_v,
        module_wp=wp,
        module_count=module_count,
        array_dc_kwp=array_dc_kwp,
        dc_ac_ratio=dc_ac_ratio,
        strings_count=strings_count,
        modules_per_string=modules_per_string,
        string_voc_stc_v=string_voc_stc_v,
        string_voc_cold_0c_v=string_voc_cold_0c_v,
        string_vmp_stc_v=string_vmp_stc_v,
        string_vmp_hot_70c_v=string_vmp_hot_70c_v,
        string_isc_max_a=string_isc_max_a,
        dc_cable_mm2=dc_cable_mm2,
        dc_cable_spec=dc_cable_spec,
        dc_run_length_m=dc_run_length_m,
        dc_voltage_drop_pct=dc_vd_pct,
        dc_isolator_spec=dc_isolator_spec,
        dc_spd_spec=dc_spd_spec,
        voc_cold_pass=voc_cold_pass,
        mppt_window_pass=mppt_window_pass,
        dc_vd_pass=dc_vd_pass,
        battery_enabled=battery_enabled,
        battery_nominal_kwh=batt_kwh,
        battery_usable_kwh=battery_usable_kwh,
        battery_bus_voltage_v=battery_bus_v,
        battery_max_power_kw=battery_max_kw,
        battery_max_current_a=battery_max_i_a,
        battery_dc_breaker_spec=battery_dc_breaker_spec,
        battery_dc_cable_spec=battery_dc_cable_spec,
        eps_backup_enabled=eps_enabled,
        eps_changeover_spec=eps_spec,
    )

    flags: list[ReviewFlag] = []
    if not voc_cold_pass:
        flags.append(
            ReviewFlag(
                code="PV_DC_VOC_EXCEEDS_INVERTER_MAX",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                regulation_ref="BS 7671 Reg 712.512.1.1 / IEC 62548",
                message=(
                    f"PV string cold open-circuit voltage Voc(0°C)={string_voc_cold_0c_v:.1f}V "
                    f"exceeds inverter Vdc_max={inv_max_dc_v}V. Reduce modules per string."
                ),
                requires_etek_signoff=True,
            )
        )
    if not mppt_window_pass:
        flags.append(
            ReviewFlag(
                code="PV_MPPT_HOT_WINDOW_WARNING",
                severity=ReviewSeverity.WARNING,
                category="bs7671",
                regulation_ref="IEC 62548 / Inverter MPPT Window",
                message=(
                    f"PV string hot operating voltage Vmp(70°C)={string_vmp_hot_70c_v:.1f}V "
                    f"falls outside inverter MPPT window ({mppt_min_v}-{mppt_max_v}V)."
                ),
                requires_etek_signoff=True,
            )
        )
    if battery_enabled:
        flags.append(
            ReviewFlag(
                code="EAC_2026_BESS_STORAGE_ENGINEERED",
                severity=ReviewSeverity.INFO,
                category="eac",
                regulation_ref=(
                    "EAC Technical Guide for Storage System Connections 2026 (DRG. PVS-S-1.2)"
                ),
                message=(
                    f"Battery storage {batt_kwh:g} kWh LiFePO4 ({battery_bus_v}V DC, "
                    f"max {battery_max_kw:g} kW / {battery_max_i_a:g} A, "
                    f"{battery_dc_breaker_spec}) sized with EAC R1 Curtailment + "
                    "R2 Zero-Export ripple receivers."
                ),
                requires_etek_signoff=False,
            )
        )
    return res, flags

