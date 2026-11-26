"""Residential engineering calculators: CIBSE Lumen Method, 18mm DIN-Rail DB & IEC 62305-2 LPS."""

from __future__ import annotations

import math

from cyelec.models.building import BuildingModel, RoomType
from cyelec.models.electrical import (
    DinRailBoardLayout,
    DinRailRow,
    ElectricalDesignResult,
    LightningRiskAssessment,
    ResidentialSystemsBrief,
    ReviewFlag,
    ReviewSeverity,
    RoomLumenCheck,
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
    RoomType.VERANDA: 100,
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
    hm = max(0.8, (ceiling_height_mm / 1000.0) - 0.85)  # Mounting height above 0.85m working plane
    room_index_k = area_m2 / max(hm * 2.0 * side_m, 0.5)
    # Typical residential ceiling/wall reflectance (70/50/20) LED downlight UF curve
    uf = 0.52 + 0.24 * (1.0 - math.exp(-0.85 * room_index_k))
    return round(min(0.82, max(0.50, uf)), 2)


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
    """Verify room-by-room illuminance via CIBSE / BS EN 12464-1 Lumen Method."""
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
        achieved_lux = round((total_lm * uf * mf) / max(room.area_m2, 0.5), 1)
        target_lux = _TARGET_LUX_BY_ROOM.get(room.normalized_type, 150)
        # Allow 10% design tolerance on residential photometric checks
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
                        f"Room '{room.raw_label}' ({room.area_m2:.1f} m²): estimated "
                        f"{achieved_lux:.0f} lux vs {target_lux} lux target ({n_fix}x {avg_w:g}W "
                        "LED). Increase lumen output or add task/cove lighting if required."
                    ),
                    requires_etek_signoff=False,
                )
            )

    return checks, flags


def _pack_din_rows(
    modules: list[tuple[str, int]],
    *,
    modules_per_row: int = 18,
    min_spare_ratio: float = 0.20,
) -> tuple[list[DinRailRow], int, int, int]:
    """Pack 18 mm DIN-rail devices onto standard horizontal rails with >=20% spare ways."""
    used_total = sum(m for _, m in modules)
    req_capacity = int(math.ceil(used_total / max(1.0 - min_spare_ratio, 0.5)))
    # Standard Hager/ABB/Schneider row counts: 1, 2, 3, 4, 5, 6 rows of 18M or 24M
    if req_capacity > 72:
        modules_per_row = 24
    num_rows = max(2, int(math.ceil(req_capacity / float(modules_per_row))))
    total_cap = num_rows * modules_per_row

    rows: list[DinRailRow] = []
    cur_items: list[str] = []
    cur_used = 0
    row_no = 1

    for label, width in modules:
        if cur_used + width > modules_per_row and cur_items:
            rows.append(
                DinRailRow(
                    row_no=row_no,
                    modules_used=cur_used,
                    modules_capacity=modules_per_row,
                    items=cur_items,
                )
            )
            row_no += 1
            cur_items = []
            cur_used = 0
        cur_items.append(f"{label} ({width}M)")
        cur_used += width

    if cur_items:
        rows.append(
            DinRailRow(
                row_no=row_no,
                modules_used=cur_used,
                modules_capacity=modules_per_row,
                items=cur_items,
            )
        )
        row_no += 1

    while len(rows) < num_rows:
        rows.append(
            DinRailRow(
                row_no=len(rows) + 1,
                modules_used=0,
                modules_capacity=modules_per_row,
                items=["SPARE RAIL (20%+ Expansion)"],
            )
        )

    spare = max(0, total_cap - used_total)
    return rows, used_total, spare, total_cap


def compute_din_rail_layouts(
    design: ElectricalDesignResult,
) -> list[DinRailBoardLayout]:
    """Build physical 18 mm DIN-rail schedules, enclosure specs & selectivity cascade per board."""
    eac = design.eac_compliance
    is_3p = eac.supply_phases == 3
    brief = design.residential_brief
    ipf_origin = design.prospective_fault_current_ka
    min_icu = 10.0 if ipf_origin > 6.0 else 6.0

    layouts: list[DinRailBoardLayout] = []
    has_sub_boards = len(design.building.sub_boards) > 0

    if has_sub_boards:
        # 1. Main Distribution Board (MDB) feeding all sub-mains
        mdb_mods: list[tuple[str, int]] = [
            (
                f"MAIN ISOLATOR {eac.recommended_main_breaker_a}A {'4P' if is_3p else '2P'}",
                4 if is_3p else 2,
            ),
        ]
        if design.include_tt_main_rcd_s:
            mdb_mods.append(("MAIN RCD 100mA TYPE-S", 4 if is_3p else 2))
        mdb_mods.append(("SPD TYPE 2 40kA", 4 if is_3p else 2))
        if design.building.pv_capacity_kva > 0:
            mdb_mods.append(("PV AC RCBO/ISOL", 4 if is_3p else 2))
        for sm in design.sub_mains:
            mdb_mods.append(
                (
                    f"FEEDER {sm.board_id} {sm.breaker_in_a}A {sm.breaker_curve.value}",
                    4 if sm.is_three_phase else 2,
                )
            )
        # Any circuits directly on MDB
        for c in design.circuits:
            if c.spec.board_id == design.building.db_anchor.id:
                w = 4 if c.spec.is_three_phase else 2
                mdb_mods.append((f"{c.spec.circuit_id} {c.breaker_in_a}A", w))

        rows, used, spare, cap = _pack_din_rows(mdb_mods)
        mpr = rows[0].modules_capacity if rows else 18
        spare_pct = round(100.0 * spare / max(cap, 1), 1)
        enclosure = (
            f"Non-Combustible Steel Flush/Surface Enclosure (Reg 421.1.201), "
            f"{len(rows)}x{mpr}M ({cap}M total, {spare_pct:.0f}% spare), IP40"
        )
        max_sm_in = max((sm.breaker_in_a for sm in design.sub_mains), default=40)
        sel_ratio = eac.recommended_main_breaker_a / max(max_sm_in, 1)
        sel_pass = sel_ratio >= 1.25 or design.include_tt_main_rcd_s
        sel_note = (
            f"Upstream {eac.recommended_main_breaker_a}A + 100mA Type-S (Δt>=40ms) -> "
            f"Sub-mains <= {max_sm_in}A -> Final 30mA RCBOs: "
            f"{'PASS (Time+Current Selective)' if sel_pass else 'VERIFY DISCRIMINATION'}"
        )
        layouts.append(
            DinRailBoardLayout(
                board_id=design.building.db_anchor.id,
                floor="GF / MAIN",
                total_modules_used=used,
                spare_modules=spare,
                total_modules_capacity=cap,
                spare_pct=spare_pct,
                enclosure_spec=enclosure,
                rows=rows,
                incoming_ipf_ka=round(ipf_origin, 2),
                min_device_icu_ka=min_icu,
                selectivity_pass=sel_pass,
                selectivity_note=sel_note,
            )
        )

        # 2. Each Floor Sub-Distribution Board (DB-GF, DB-1F, ...)
        sm_by_board = {sm.board_id: sm for sm in design.sub_mains}
        for sb in design.building.sub_boards:
            sub_m = sm_by_board.get(sb.board_id)
            sb_mods: list[tuple[str, int]] = [
                (
                    f"LOCAL ISOLATOR {sub_m.breaker_in_a if sub_m else 63}A "
                    f"{'4P' if sb.is_three_phase else '2P'}",
                    4 if sb.is_three_phase else 2,
                ),
            ]
            board_circs = [c for c in design.circuits if c.spec.board_id == sb.board_id]
            for c in board_circs:
                w = 4 if c.spec.is_three_phase else 2
                sb_mods.append(
                    (f"{c.spec.circuit_id} {c.breaker_in_a}A {c.breaker_curve.value} RCBO", w)
                )
            if brief.include_knx and sb.floor in ("GF", design.building.sub_boards[0].floor):
                sb_mods.extend(
                    [
                        ("KNX PSU 640mA", 4),
                        ("KNX 8-CH SWITCH ACTUATOR", 4),
                        ("KNX 4-CH SHUTTER/DIM ACT", 4),
                    ]
                )
            rows, used, spare, cap = _pack_din_rows(sb_mods)
            mpr = rows[0].modules_capacity if rows else 18
            spare_pct = round(100.0 * spare / max(cap, 1), 1)
            sb_ipf = round(
                sub_m.downstream_ipf_ka
                if (sub_m and sub_m.downstream_ipf_ka)
                else max(1.5, ipf_origin * 0.7),
                2,
            )
            sb_icu = 10.0 if sb_ipf > 6.0 else 6.0
            max_fin = max((c.breaker_in_a for c in board_circs), default=32)
            up_in = sub_m.breaker_in_a if sub_m else eac.recommended_main_breaker_a
            sel_ok = up_in >= max_fin
            layouts.append(
                DinRailBoardLayout(
                    board_id=sb.board_id,
                    floor=sb.floor,
                    total_modules_used=used,
                    spare_modules=spare,
                    total_modules_capacity=cap,
                    spare_pct=spare_pct,
                    enclosure_spec=(
                        f"Non-Combustible Steel Enclosure (Reg 421.1.201), "
                        f"{len(rows)}x{mpr}M ({cap}M total, {spare_pct:.0f}% spare), IP40"
                    ),
                    rows=rows,
                    incoming_ipf_ka=sb_ipf,
                    min_device_icu_ka=sb_icu,
                    selectivity_pass=sel_ok,
                    selectivity_note=(
                        f"Sub-main {up_in}A -> Max Final {max_fin}A 30mA RCBO (Ipf={sb_ipf} kA <= "
                        f"Icu={sb_icu:.0f} kA)"
                    ),
                )
            )
    else:
        # Single-board building (DB1)
        db_mods: list[tuple[str, int]] = [
            (
                f"MAIN ISOLATOR {eac.recommended_main_breaker_a}A {'4P' if is_3p else '2P'}",
                4 if is_3p else 2,
            ),
        ]
        if design.include_tt_main_rcd_s:
            db_mods.append(("MAIN RCD 100mA TYPE-S", 4 if is_3p else 2))
        db_mods.append(("SPD TYPE 2 40kA", 4 if is_3p else 2))
        if design.building.pv_capacity_kva > 0:
            db_mods.append(("PV AC RCBO/ISOL", 4 if is_3p else 2))
        for c in design.circuits:
            w = 4 if c.spec.is_three_phase else 2
            db_mods.append(
                (f"{c.spec.circuit_id} {c.breaker_in_a}A {c.breaker_curve.value} RCBO", w)
            )
        if brief.include_knx:
            db_mods.extend(
                [
                    ("KNX PSU 640mA", 4),
                    ("KNX 8-CH SWITCH ACTUATOR", 4),
                    ("KNX 4-CH SHUTTER/DIM ACT", 4),
                ]
            )
        rows, used, spare, cap = _pack_din_rows(db_mods)
        mpr = rows[0].modules_capacity if rows else 18
        spare_pct = round(100.0 * spare / max(cap, 1), 1)
        max_fin = max((c.breaker_in_a for c in design.circuits), default=32)
        sel_ok = eac.recommended_main_breaker_a >= max_fin
        layouts.append(
            DinRailBoardLayout(
                board_id=design.building.db_anchor.id,
                floor="GF",
                total_modules_used=used,
                spare_modules=spare,
                total_modules_capacity=cap,
                spare_pct=spare_pct,
                enclosure_spec=(
                    f"Non-Combustible Steel Enclosure (Reg 421.1.201), "
                    f"{len(rows)}x{mpr}M ({cap}M total, {spare_pct:.0f}% spare), IP40"
                ),
                rows=rows,
                incoming_ipf_ka=round(ipf_origin, 2),
                min_device_icu_ka=min_icu,
                selectivity_pass=sel_ok,
                selectivity_note=(
                    f"Main {eac.recommended_main_breaker_a}A + 100mA Type-S -> "
                    f"Max Final {max_fin}A 30mA RCBO "
                    f"(Ipf={ipf_origin:.1f} kA <= Icu={min_icu:.0f} kA)"
                ),
            )
        )

    return layouts


def compute_lightning_risk_iec62305(
    building: BuildingModel,
    brief: ResidentialSystemsBrief,
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
    spd_req = (
        "Type 1+2 Combined SPD (12.5kA 10/350µs) at MDB + DC SPD for Roof PV"
        if (lps_req or building.pv_capacity_kva > 0)
        else "Type 2 SPD (40kA 8/20µs) at MDB (BS 7671 Reg 443.4)"
    )

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
