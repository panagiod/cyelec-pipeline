"""Deterministic BS 7671 sub-main feeder & cumulative voltage drop solver (MDB -> DB-GF/DB-1F)."""

from __future__ import annotations

import math

from cyelec.models.building import SubBoardSpec
from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitSpec,
    EarthingSystem,
    ReviewFlag,
    ReviewSeverity,
    SolvedCircuit,
    SolvedSubMain,
)
from cyelec.standards_loader import CableEntry, StandardsBundle

_SUBMAIN_STANDARD_RATINGS_A: tuple[int, ...] = (16, 20, 25, 32, 40, 50, 63, 80, 100, 125)


def _select_submain_rating_a(ib_a: float, *, min_rating_a: int = 32) -> int:
    target = max(ib_a, float(min_rating_a))
    for rating in _SUBMAIN_STANDARD_RATINGS_A:
        if rating >= target - 1e-9:
            return rating
    return _SUBMAIN_STANDARD_RATINGS_A[-1]


def _submain_cpc_mm2(phase_mm2: float) -> float:
    """BS 7671 Table 54.7 minimum protective conductor cross-sectional area."""
    if phase_mm2 <= 16.0:
        return phase_mm2
    if phase_mm2 <= 35.0:
        return 16.0
    return phase_mm2 / 2.0


def _tabulated_current(entry: CableEntry, sub_board: SubBoardSpec) -> float:
    if sub_board.installation_method == "B":
        return entry.method_b_3p_a if sub_board.is_three_phase else entry.method_b_1p_a
    return entry.method_c_3p_a if sub_board.is_three_phase else entry.method_c_1p_a


def solve_submain(
    sub_board: SubBoardSpec,
    board_specs: list[CircuitSpec],
    standards: StandardsBundle,
    *,
    earthing_system: EarthingSystem = EarthingSystem.TT,
    ze_or_ra_ohm: float | None = None,
    target_submain_vd_pct: float = 1.0,
) -> SolvedSubMain:
    """Size and verify a BS 7671 sub-main feeder from MDB to a floor sub-distribution board."""
    cables_std = standards.cables
    devices_std = standards.devices
    eac_std = standards.eac_rules

    resolved_ze_ra = (
        ze_or_ra_ohm
        if ze_or_ra_ohm is not None
        else eac_std.earthing.default_ze_or_ra_ohm[earthing_system]
    )

    connected_kva = round(
        sum(s.connected_load_w / (1000.0 * s.power_factor) for s in board_specs), 2
    )
    diversified_kva = round(
        sum(s.diversified_load_w / (1000.0 * s.power_factor) for s in board_specs), 2
    )

    u0 = eac_std.system_nominal.u0_v
    ul = eac_std.system_nominal.ul_v
    v_nom = ul if sub_board.is_three_phase else u0
    ib_a = (
        (diversified_kva * 1000.0) / (math.sqrt(3.0) * ul)
        if sub_board.is_three_phase
        else (diversified_kva * 1000.0) / u0
    )
    ib_a = round(max(1.0, ib_a), 2)

    # Preserve discrimination above 32 A socket rings (min 40 A for residential sub-boards)
    min_submain_in = 40 if any((s.min_breaker_in_a or 0) >= 32 for s in board_specs) else 32
    breaker_in_a = _select_submain_rating_a(ib_a, min_rating_a=min_submain_in)
    curve = BreakerCurve.C if sub_board.is_three_phase else BreakerCurve.B

    flags: list[ReviewFlag] = []
    if breaker_in_a > max(devices_std.standard_ratings_a):
        # BS 7671 5-second distribution circuit MCCB fallback (Table 41.4/manufacturer data)
        zs_max_ohm = round((u0 * 0.95) / (breaker_in_a * 5.0), 3)
        flags.append(
            ReviewFlag(
                code="SUBMAIN_MCCB_SELECTIVITY_VERIFY",
                severity=ReviewSeverity.INFO,
                category="bs7671",
                regulation_ref="BS 7671 Reg 536.4 / Table 41.4",
                message=(
                    f"Sub-main {sub_board.parent_board_id}->{sub_board.board_id} requires "
                    f"{breaker_in_a} A MCCB/switch-fuse (>63 A MCB range); ETEK engineer to "
                    "confirm manufacturer time-current selectivity curve."
                ),
            )
        )
    else:
        zs_max_ohm = devices_std.get_device(curve, breaker_in_a).zs_max_ohm

    # TT earthing: mandatory Type S time-delayed selective RCD upstream (Reg 536.4.1.4)
    rcd_selective_type_s = earthing_system == EarthingSystem.TT
    rcd_idn_ma: int | None = 100 if rcd_selective_type_s else None
    poles = "4P" if sub_board.is_three_phase else "2P"
    if rcd_selective_type_s:
        device_label = (
            f"{breaker_in_a}A {poles} Type {curve.value} MCB/MCCB + "
            f"{rcd_idn_ma}mA Type S Selective RCD"
        )
    else:
        device_label = f"{breaker_in_a}A {poles} Type {curve.value} MCB/MCCB"

    ca = cables_std.get_ca(sub_board.ambient_temp_c, "pvc_70c")
    cg = 1.0
    ci = 1.0
    eff_derating = ca * cg * ci
    temp_mult = cables_std.constants.temp_resistance_multiplier_70c

    candidates = [c for c in cables_std.cables if c.size_mm2 >= 6.0]
    chosen: CableEntry | None = None
    best_eval: tuple[float, float, bool, float, float, float, float, bool] | None = None

    for entry in candidates:
        it_a = _tabulated_current(entry, sub_board)
        iz_a = it_a * eff_derating
        thermal_ok = iz_a >= breaker_in_a - 1e-9

        mv_a_m = entry.mv_per_a_per_m_3p if sub_board.is_three_phase else entry.mv_per_a_per_m_1p
        vd_v = (mv_a_m * ib_a * sub_board.submain_length_m) / 1000.0
        vd_pct = (vd_v / v_nom) * 100.0
        vd_ok = vd_pct <= target_submain_vd_pct + 1e-9

        cpc_mm2 = _submain_cpc_mm2(entry.size_mm2)
        r1 = entry.r20_mohm_per_m
        r2 = cables_std.get_r20_mohm_per_m(cpc_mm2)
        r1_r2 = ((r1 + r2) * sub_board.submain_length_m / 1000.0) * temp_mult
        r2_ohm = (r2 * sub_board.submain_length_m / 1000.0) * temp_mult
        zs_ohm = resolved_ze_ra + r1_r2

        if earthing_system == EarthingSystem.TT:
            assert rcd_idn_ma is not None
            touch_v = (resolved_ze_ra + r2_ohm) * (rcd_idn_ma / 1000.0)
            ef_ok = touch_v <= eac_std.earthing.tt_touch_voltage_limit_v + 1e-9
        else:
            ef_ok = zs_ohm <= zs_max_ohm + 1e-9

        best_eval = (it_a, iz_a, thermal_ok, vd_v, vd_pct, r1_r2, zs_ohm, ef_ok)
        chosen = entry
        if thermal_ok and vd_ok and ef_ok:
            break

    assert chosen is not None and best_eval is not None
    it_a, iz_a, thermal_ok, vd_v, vd_pct, r1_r2, zs_ohm, ef_ok = best_eval
    cpc_mm2 = _submain_cpc_mm2(chosen.size_mm2)

    if rcd_selective_type_s:
        if ef_ok:
            flags.append(
                ReviewFlag(
                    code="SUBMAIN_TT_SELECTIVE_RCD",
                    severity=ReviewSeverity.INFO,
                    category="bs7671",
                    regulation_ref="BS 7671 Reg 536.4.1.4 & Reg 411.5.3",
                    message=(
                        f"TT sub-main {sub_board.parent_board_id}->{sub_board.board_id} "
                        f"protected by {rcd_idn_ma} mA Type S time-delayed RCD for vertical "
                        f"selectivity with downstream 30 mA final-circuit RCBOs "
                        f"(Ra={resolved_ze_ra:.1f} Ω)."
                    ),
                )
            )
        else:
            flags.append(
                ReviewFlag(
                    code="SUBMAIN_TT_TOUCH_VOLTAGE_EXCEEDED",
                    severity=ReviewSeverity.BLOCKER,
                    category="bs7671",
                    regulation_ref="BS 7671 Reg 411.5.3",
                    message=(
                        f"TT sub-main {sub_board.board_id} fails Ra×IΔn <= 50 V at "
                        f"Ra={resolved_ze_ra:.1f} Ω with {rcd_idn_ma} mA Type S RCD; "
                        "improve earth electrode resistance."
                    ),
                )
            )

    if not thermal_ok:
        flags.append(
            ReviewFlag(
                code="SUBMAIN_THERMAL_EXCEEDS_TABLE",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                regulation_ref="BS 7671 Appendix 4",
                message=(
                    f"Sub-main {sub_board.board_id} ({breaker_in_a} A) exceeds max standard "
                    f"PVC table size ({chosen.size_mm2:g} mm², Iz={iz_a:.1f} A); use SWA/XLPE."
                ),
            )
        )

    if not ef_ok and earthing_system != EarthingSystem.TT:
        flags.append(
            ReviewFlag(
                code="SUBMAIN_TN_ZS_EXCEEDED",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                regulation_ref="BS 7671 Reg 411.3.2",
                message=(
                    f"Sub-main {sub_board.board_id} Zs={zs_ohm:.3f} Ω exceeds "
                    f"Zs_max={zs_max_ohm:.3f} Ω."
                ),
            )
        )

    return SolvedSubMain(
        board_id=sub_board.board_id,
        parent_board_id=sub_board.parent_board_id,
        floor=sub_board.floor,
        circuit_ids=[s.circuit_id for s in board_specs],
        connected_kva=connected_kva,
        diversified_kva=diversified_kva,
        design_current_ib_a=ib_a,
        is_three_phase=sub_board.is_three_phase,
        breaker_in_a=breaker_in_a,
        breaker_curve=curve,
        rcd_selective_type_s=rcd_selective_type_s,
        rcd_idn_ma=rcd_idn_ma,
        device_label=device_label,
        cable_mm2=chosen.size_mm2,
        cpc_mm2=cpc_mm2,
        length_m=sub_board.submain_length_m,
        installation_method=sub_board.installation_method,
        ca=round(ca, 3),
        cg=cg,
        ci=ci,
        it_tabulated_a=round(it_a, 2),
        iz_derated_a=round(iz_a, 2),
        thermal_pass=thermal_ok,
        voltage_drop_v=round(vd_v, 2),
        voltage_drop_pct=round(vd_pct, 2),
        r1_plus_r2_ohm=round(r1_r2, 4),
        zs_ohm=round(zs_ohm, 4),
        zs_max_ohm=round(zs_max_ohm, 3),
        earth_fault_pass=ef_ok,
        overall_pass=thermal_ok and ef_ok,
        flags=flags,
    )


def finalize_submain_cumulative_vd(
    sub_main: SolvedSubMain,
    board_solved_circuits: list[SolvedCircuit],
    standards: StandardsBundle,
) -> SolvedSubMain:
    """Compute and verify cumulative BS 7671 voltage drop (sub-main + worst final circuit)."""
    lim_light = standards.eac_rules.voltage_drop_limits_pct.lighting
    lim_power = standards.eac_rules.voltage_drop_limits_pct.power

    max_light = max(
        (
            c.voltage_drop_pct
            for c in board_solved_circuits
            if c.spec.category == CircuitCategory.LIGHTING
        ),
        default=0.0,
    )
    max_power = max(
        (
            c.voltage_drop_pct
            for c in board_solved_circuits
            if c.spec.category != CircuitCategory.LIGHTING
        ),
        default=0.0,
    )
    cum_light = round(sub_main.voltage_drop_pct + max_light, 2)
    cum_power = round(sub_main.voltage_drop_pct + max_power, 2)
    cum_ok = (cum_light <= lim_light + 1e-6) and (cum_power <= lim_power + 1e-6)

    extra_flags = list(sub_main.flags)
    if not cum_ok:
        extra_flags.append(
            ReviewFlag(
                code="SUBMAIN_CUMULATIVE_VD_EXCEEDED",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                regulation_ref="BS 7671 Appendix 4 Table 4Ab",
                message=(
                    f"Board {sub_main.board_id} cumulative ΔV exceeds BS 7671 limits: "
                    f"lighting={cum_light:.2f}% (limit {lim_light:.1f}%), "
                    f"power={cum_power:.2f}% (limit {lim_power:.1f}%)."
                ),
            )
        )

    return sub_main.model_copy(
        update={
            "max_final_lighting_vd_pct": round(max_light, 2),
            "max_final_power_vd_pct": round(max_power, 2),
            "cumulative_lighting_vd_pct": cum_light,
            "cumulative_power_vd_pct": cum_power,
            "cumulative_vd_pass": cum_ok,
            "overall_pass": sub_main.thermal_pass and sub_main.earth_fault_pass and cum_ok,
            "flags": extra_flags,
        }
    )
