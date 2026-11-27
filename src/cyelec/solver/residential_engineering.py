"""Residential engineering calculators: CIBSE Lumen Method, 18mm DIN-Rail DB & IEC 62305-2 LPS."""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.building import BuildingModel, RoomType
from cyelec.models.electrical import (
    DinRailBoardLayout,
    DinRailRow,
    ElectricalDesignResult,
    LightningRiskAssessment,
    PvStorageEngineeringResult,
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

