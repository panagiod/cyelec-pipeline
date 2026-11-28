"""Deterministic BS 7671:2018+A2 cable sizing, voltage drop, Zs/TT RCD, and adiabatic CPC solver."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitOverride,
    CircuitSpec,
    CircuitTopology,
    EarthingSystem,
    PhaseAssignment,
    ReviewFlag,
    ReviewSeverity,
    SolvedCircuit,
)
from cyelec.standards_loader import CableEntry, StandardsBundle


@dataclass(frozen=True)
class _CandidateEval:
    cable_mm2: float
    cpc_mm2: float
    it_a: float
    iz_a: float
    thermal_pass: bool
    mv_per_a_per_m: float
    vd_v: float
    vd_pct: float
    vd_pass: bool
    r1_plus_r2_ohm: float
    r2_ohm: float
    zs_ohm: float
    earth_fault_pass: bool
    tt_touch_voltage_v: float | None
    cpc_min_mm2: float
    cpc_adiabatic_pass: bool
    live_adiabatic_pass: bool

    @property
    def all_pass(self) -> bool:
        return (
            self.thermal_pass
            and self.vd_pass
            and self.earth_fault_pass
            and self.cpc_adiabatic_pass
            and self.live_adiabatic_pass
        )


# BS 7671 Appendix 5 / OSG Table 5E approximate insulated conductor cross-sectional area (mm²)
_CONDUCTOR_OUTER_AREA_MM2: dict[float, float] = {
    1.5: 9.6,
    2.5: 13.8,
    4.0: 18.8,
    6.0: 25.5,
    10.0: 40.7,
    16.0: 58.1,
    25.0: 88.2,
    35.0: 116.9,
    50.0: 162.9,
    70.0: 216.4,
    95.0: 289.5,
    120.0: 356.3,
    150.0: 441.2,
    185.0: 547.4,
    240.0: 706.9,
}


def size_conduit_or_tray(
    cable_mm2: float,
    *,
    is_three_phase: bool,
    grouping_count: int = 1,
) -> str:
    """Size PVC conduit (20/25/32 mm) or galvanized cable tray per BS 7671 45% space factor."""
    if cable_mm2 >= 25.0 or (is_three_phase and cable_mm2 >= 16.0):
        return "Galv. Cable Tray"
    cores = 5 if is_three_phase else 3
    per_core = _CONDUCTOR_OUTER_AREA_MM2.get(cable_mm2, cable_mm2 * 3.2)
    total_fill_mm2 = per_core * cores * max(1, grouping_count)
    # 45% usable internal bore area of heavy-duty PVC conduit (ID 16.4 / 21.0 / 27.6 mm)
    if total_fill_mm2 <= 95.0:
        return "20mm PVC"
    if total_fill_mm2 <= 155.0:
        return "25mm PVC"
    if total_fill_mm2 <= 265.0:
        return "32mm PVC"
    return "Galv. Cable Tray"


def tabulated_current(
    entry: CableEntry,
    method: Literal["B", "C"],
    is_three_phase: bool,
    insulation_type: Literal["pvc_70c", "xlpe_90c"] = "pvc_70c",
) -> float:
    """Return BS 7671 Table 4D2A/4E4A tabulated current-carrying capacity It (A)."""
    if insulation_type == "xlpe_90c":
        if method == "B":
            val = entry.xlpe_method_b_3p_a if is_three_phase else entry.xlpe_method_b_1p_a
            if val is not None:
                return val
        else:
            val = entry.xlpe_method_c_3p_a if is_three_phase else entry.xlpe_method_c_1p_a
            if val is not None:
                return val
    if method == "B":
        return entry.method_b_3p_a if is_three_phase else entry.method_b_1p_a
    return entry.method_c_3p_a if is_three_phase else entry.method_c_1p_a


_tabulated_current = tabulated_current


def _mv_per_a_per_m(entry: CableEntry, is_three_phase: bool) -> float:
    return entry.mv_per_a_per_m_3p if is_three_phase else entry.mv_per_a_per_m_1p


def _evaluate_cable(
    *,
    entry: CableEntry,
    cpc_mm2: float,
    spec: CircuitSpec,
    standards: StandardsBundle,
    required_iz_a: float,
    effective_derating: float,
    earthing_system: EarthingSystem,
    ze_or_ra_ohm: float,
    zs_max_ohm: float,
    i2t_a2s: float,
) -> _CandidateEval:
    cables_std = standards.cables
    eac_std = standards.eac_rules

    it_a = _tabulated_current(
        entry, spec.installation_method, spec.is_three_phase, spec.insulation_type
    )
    iz_a = it_a * effective_derating
    thermal_pass = iz_a >= required_iz_a - 1e-9

    mv_a_m = _mv_per_a_per_m(entry, spec.is_three_phase)
    if spec.electrical_path_length_m is not None:
        effective_length_m = spec.electrical_path_length_m
    else:
        effective_length_m = (
            spec.length_m / 4.0 if spec.topology == CircuitTopology.RING_FINAL else spec.length_m
        )
    vd_dist = spec.distributed_vd_factor if spec.distributed_vd_factor is not None else 1.0
    vd_v = (mv_a_m * spec.design_current_ib_a * effective_length_m * vd_dist) / 1000.0
    vd_pct = (vd_v / spec.nominal_voltage_v) * 100.0
    vd_pass = vd_pct <= spec.vd_limit_pct + 1e-9

    is_xlpe = spec.insulation_type == "xlpe_90c"
    temp_mult = (
        cables_std.constants.temp_resistance_multiplier_90c
        if is_xlpe
        else cables_std.constants.temp_resistance_multiplier_70c
    )
    r1_mohm_m = entry.r20_mohm_per_m
    r2_mohm_m = cables_std.get_r20_mohm_per_m(cpc_mm2)
    r1_plus_r2_ohm = ((r1_mohm_m + r2_mohm_m) * effective_length_m / 1000.0) * temp_mult
    r2_ohm = (r2_mohm_m * effective_length_m / 1000.0) * temp_mult
    zs_ohm = ze_or_ra_ohm + r1_plus_r2_ohm

    tt_touch_v: float | None = None
    if earthing_system == EarthingSystem.TT:
        if not spec.rcd_protected or spec.rcd_idn_ma is None:
            earth_fault_pass = False
        else:
            idn_a = spec.rcd_idn_ma / 1000.0
            tt_touch_v = (ze_or_ra_ohm + r2_ohm) * idn_a
            earth_fault_pass = tt_touch_v <= eac_std.earthing.tt_touch_voltage_limit_v + 1e-9
    else:
        earth_fault_pass = zs_ohm <= zs_max_ohm + 1e-9

    k_val = (
        cables_std.constants.adiabatic_k_xlpe_copper
        if is_xlpe
        else cables_std.constants.adiabatic_k_pvc_copper
    )
    cpc_min_mm2 = math.sqrt(i2t_a2s) / k_val
    cpc_adiabatic_pass = cpc_mm2 >= cpc_min_mm2 - 1e-9
    # BS 7671 Reg 434.5.2: live phase conductor short-circuit adiabatic withstand (k²·S² >= I²t)
    live_adiabatic_pass = entry.size_mm2 >= cpc_min_mm2 - 1e-9

    return _CandidateEval(
        cable_mm2=entry.size_mm2,
        cpc_mm2=cpc_mm2,
        it_a=it_a,
        iz_a=iz_a,
        thermal_pass=thermal_pass,
        mv_per_a_per_m=mv_a_m,
        vd_v=vd_v,
        vd_pct=vd_pct,
        vd_pass=vd_pass,
        r1_plus_r2_ohm=r1_plus_r2_ohm,
        r2_ohm=r2_ohm,
        zs_ohm=zs_ohm,
        earth_fault_pass=earth_fault_pass,
        tt_touch_voltage_v=tt_touch_v,
        cpc_min_mm2=cpc_min_mm2,
        cpc_adiabatic_pass=cpc_adiabatic_pass,
        live_adiabatic_pass=live_adiabatic_pass,
    )


def _apply_circuit_override(
    spec: CircuitSpec,
    override: CircuitOverride | None,
) -> CircuitSpec:
    if override is None:
        return spec

    updates: dict[str, object] = {}
    if override.phase is not None:
        updates["fixed_phase"] = override.phase
    if override.breaker_curve is not None:
        updates["preferred_curve"] = override.breaker_curve
    if override.length_m is not None:
        updates["length_m"] = override.length_m
    if override.grouping_count is not None:
        updates["grouping_count"] = override.grouping_count
    if override.ambient_temp_c is not None:
        updates["ambient_temp_c"] = override.ambient_temp_c
    if override.rcd_protected is not None:
        updates["rcd_protected"] = override.rcd_protected
    if override.rcd_idn_ma is not None:
        updates["rcd_idn_ma"] = override.rcd_idn_ma
    if override.diversity_factor is not None:
        diversified_w = spec.connected_load_w * override.diversity_factor
        denom = (
            math.sqrt(3.0) * spec.nominal_voltage_v * spec.power_factor
            if spec.is_three_phase
            else spec.nominal_voltage_v * spec.power_factor
        )
        updates["diversity_factor"] = override.diversity_factor
        updates["diversified_load_w"] = diversified_w
        updates["design_current_ib_a"] = diversified_w / denom

    if not updates:
        return spec
    return spec.model_copy(update=updates)


def solve_circuit(
    spec: CircuitSpec,
    standards: StandardsBundle,
    *,
    earthing_system: EarthingSystem = EarthingSystem.TT,
    ze_or_ra_ohm: float | None = None,
    phase: PhaseAssignment = PhaseAssignment.L1,
    override: CircuitOverride | None = None,
) -> SolvedCircuit:
    """Deterministically size and verify a final circuit to BS 7671:2018+A2 and Cyprus EAC rules."""
    eff_spec = _apply_circuit_override(spec, override)
    cables_std = standards.cables
    devices_std = standards.devices
    eac_std = standards.eac_rules

    resolved_ze_ra = (
        ze_or_ra_ohm
        if ze_or_ra_ohm is not None
        else eac_std.earthing.default_ze_or_ra_ohm[earthing_system]
    )

    flags: list[ReviewFlag] = []

    # 1. Derating factors (Tables 4B1, 4C1, 52.2)
    ca = cables_std.get_ca(eff_spec.ambient_temp_c, eff_spec.insulation_type)
    cg, cg_exceeded = cables_std.get_cg(eff_spec.grouping_count)
    ci = cables_std.get_ci(eff_spec.thermal_insulation_mm)
    effective_derating = ca * cg * ci

    if cg_exceeded:
        flags.append(
            ReviewFlag(
                code="GROUPING_EXCEEDS_TABLE_4C1",
                severity=ReviewSeverity.WARNING,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Table 4C1",
                message=(
                    f"Circuit {eff_spec.circuit_id} has {eff_spec.grouping_count} grouped circuits "
                    "(>20); clamped to Cg=0.38. Engineer verification required."
                ),
            )
        )

    # 2. Protective device selection (MCB B/C/D <= 63 A, MCCB / BS 88-2 > 63 A)
    curve: BreakerCurve = eff_spec.preferred_curve
    allow_mccb = (
        eff_spec.is_three_phase
        or curve in (BreakerCurve.MCCB, BreakerCurve.BS88)
        or eff_spec.design_current_ib_a > max(devices_std.standard_ratings_a)
    )
    if override is not None and override.breaker_in_a is not None:
        breaker_in_a = override.breaker_in_a
    elif eff_spec.topology == CircuitTopology.RING_FINAL:
        breaker_in_a = eff_spec.min_breaker_in_a or 32
    else:
        breaker_in_a = devices_std.select_rating(
            eff_spec.design_current_ib_a,
            min_rating_a=eff_spec.min_breaker_in_a,
            allow_mccb=allow_mccb,
        )

    if breaker_in_a > max(devices_std.standard_ratings_a) and curve not in (
        BreakerCurve.MCCB,
        BreakerCurve.BS88,
    ):
        curve = BreakerCurve.MCCB

    device_spec = devices_std.get_device(curve, breaker_in_a)
    zs_max_ohm = device_spec.zs_max_ohm
    i2t_a2s = device_spec.i2t_a2s

    if curve == BreakerCurve.BS88:
        rcd_suffix = f" + {eff_spec.rcd_idn_ma}mA RCD" if (
            eff_spec.rcd_protected and eff_spec.rcd_idn_ma is not None
        ) else ""
        device_label = f"{breaker_in_a}A BS 88-2 gG Fuse{rcd_suffix}"
    elif curve == BreakerCurve.MCCB or breaker_in_a > max(devices_std.standard_ratings_a):
        rcd_suffix = f" + {eff_spec.rcd_idn_ma}mA RCD" if (
            eff_spec.rcd_protected and eff_spec.rcd_idn_ma is not None
        ) else ""
        device_label = f"{breaker_in_a}A MCCB{rcd_suffix}"
    elif eff_spec.rcd_protected and eff_spec.rcd_idn_ma is not None:
        if eff_spec.category == CircuitCategory.EV_CHARGER:
            device_label = (
                f"{breaker_in_a}A Type {curve.value} RCBO {eff_spec.rcd_idn_ma}mA "
                "(Type A + 6mA DC)"
            )
        else:
            device_label = f"{breaker_in_a}A Type {curve.value} RCBO {eff_spec.rcd_idn_ma}mA"
    else:
        device_label = f"{breaker_in_a}A Type {curve.value} MCB"

    if eff_spec.insulation_type == "xlpe_90c" and breaker_in_a <= max(
        devices_std.standard_ratings_a
    ):
        flags.append(
            ReviewFlag(
                code="XLPE_CONSERVATIVE_70C_BASE_IT",
                severity=ReviewSeverity.INFO,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Reg 512.1.5 / Table 4B1",
                message=(
                    f"Circuit {eff_spec.circuit_id} uses 90°C XLPE Ca/k factors against "
                    "conservative 70°C accessory-limited base It ratings."
                ),
            )
        )

    if eff_spec.category == CircuitCategory.EV_CHARGER:
        flags.append(
            ReviewFlag(
                code="EV_RCD_TYPE_A_DC_REQUIRED",
                severity=ReviewSeverity.INFO,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Reg 722.531.3.101",
                message=(
                    f"EV circuit {eff_spec.circuit_id} requires Type A 30 mA RCD plus "
                    "6 mA DC RDC-DD (or Type B RCD) dedicated to the charge point."
                ),
            )
        )
        if earthing_system == EarthingSystem.TN_C_S:
            flags.append(
                ReviewFlag(
                    code="EV_PME_PEN_PROTECTION_REQUIRED",
                    severity=ReviewSeverity.BLOCKER,
                    category="bs7671",
                    circuit_id=eff_spec.circuit_id,
                    regulation_ref="BS 7671 Reg 722.411.4.1",
                    message=(
                        f"EV circuit {eff_spec.circuit_id} on TN-C-S (PME) earthing requires "
                        "certified open-PEN fault detection or an isolated TT earth electrode."
                    ),
                )
            )

    # Required Iz: BS 7671 Reg 433.1.204 (20 A leg rule for 32 A ring final) vs Reg 433.1.1 (In)
    if eff_spec.topology == CircuitTopology.RING_FINAL:
        required_iz_a = max(cables_std.constants.ring_final_min_iz_a, 0.67 * breaker_in_a)
        if breaker_in_a == 32:
            required_iz_a = cables_std.constants.ring_final_min_iz_a
    else:
        required_iz_a = float(breaker_in_a)

    # 3. Cable selection & deterministic upsizing
    all_cables = cables_std.sorted_cables()
    floor_cable_mm2 = eff_spec.min_cable_mm2
    if eff_spec.topology == CircuitTopology.RING_FINAL:
        floor_cable_mm2 = max(floor_cable_mm2, cables_std.constants.ring_final_min_cable_mm2)

    fixed_cable = override.cable_mm2 if override is not None else None
    fixed_cpc = override.cpc_mm2 if override is not None else None

    if fixed_cable is not None:
        candidates = [cables_std.get_cable(fixed_cable)]
    else:
        candidates = [c for c in all_cables if c.size_mm2 >= floor_cable_mm2 - 1e-9]
        if not candidates:
            candidates = [all_cables[-1]]

    upsize_reasons: list[str] = []
    chosen: _CandidateEval | None = None

    for idx, entry in enumerate(candidates):
        cpc_mm2 = fixed_cpc if fixed_cpc is not None else entry.default_cpc_mm2
        ev = _evaluate_cable(
            entry=entry,
            cpc_mm2=cpc_mm2,
            spec=eff_spec,
            standards=standards,
            required_iz_a=required_iz_a,
            effective_derating=effective_derating,
            earthing_system=earthing_system,
            ze_or_ra_ohm=resolved_ze_ra,
            zs_max_ohm=zs_max_ohm,
            i2t_a2s=i2t_a2s,
        )
        chosen = ev

        if ev.all_pass:
            break

        # If this isn't the last candidate, record why we are upsizing
        if idx < len(candidates) - 1:
            if not ev.thermal_pass and "thermal" not in upsize_reasons:
                upsize_reasons.append("thermal")
            if not ev.vd_pass and "voltage_drop" not in upsize_reasons:
                upsize_reasons.append("voltage_drop")
            if (
                earthing_system != EarthingSystem.TT
                and not ev.earth_fault_pass
                and "zs_impedance" not in upsize_reasons
            ):
                upsize_reasons.append("zs_impedance")
            if not ev.cpc_adiabatic_pass and "cpc_adiabatic" not in upsize_reasons:
                upsize_reasons.append("cpc_adiabatic")
            if not ev.live_adiabatic_pass and "live_adiabatic" not in upsize_reasons:
                upsize_reasons.append("live_adiabatic")

            # On TT systems, missing RCD cannot be fixed by upsizing copper;
            # stop early if only TT earth-fault verification failed.
            if (
                earthing_system == EarthingSystem.TT
                and ev.thermal_pass
                and ev.vd_pass
                and ev.cpc_adiabatic_pass
                and ev.live_adiabatic_pass
                and not ev.earth_fault_pass
            ):
                break

    if chosen is None:
        raise RuntimeError(f"No cable candidate available for circuit '{spec.circuit_id}'.")

    # 4. Emit audit & compliance review flags
    if upsize_reasons:
        if eff_spec.topology == CircuitTopology.RING_FINAL:
            flags.append(
                ReviewFlag(
                    code="RING_LEG_UPSIZED",
                    severity=ReviewSeverity.INFO,
                    category="bs7671",
                    circuit_id=eff_spec.circuit_id,
                    regulation_ref="BS 7671 Reg 433.1.204",
                    message=(
                        f"Ring final {eff_spec.circuit_id} upsized to {chosen.cable_mm2:g} mm² "
                        f"due to: {', '.join(upsize_reasons)} (Iz={chosen.iz_a:.2f} A)."
                    ),
                )
            )
        elif "voltage_drop" in upsize_reasons:
            flags.append(
                ReviewFlag(
                    code="VD_UPSIZED",
                    severity=ReviewSeverity.INFO,
                    category="bs7671",
                    circuit_id=eff_spec.circuit_id,
                    regulation_ref="BS 7671 Appendix 4 Section 6.4",
                    message=(
                        f"Circuit {eff_spec.circuit_id} upsized to {chosen.cable_mm2:g} mm² "
                        f"to satisfy {eff_spec.vd_limit_pct:.1f}% voltage drop limit "
                        f"(ΔV={chosen.vd_pct:.2f}%)."
                    ),
                )
            )

    if not chosen.thermal_pass:
        flags.append(
            ReviewFlag(
                code="THERMAL_CAPACITY_FAIL",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Reg 433.1",
                message=(
                    f"Circuit {eff_spec.circuit_id} fails thermal capacity: "
                    f"Iz={chosen.iz_a:.2f} A < required {required_iz_a:.2f} A "
                    f"({chosen.cable_mm2:g} mm², Ca={ca}, Cg={cg}, Ci={ci})."
                ),
            )
        )

    if not chosen.vd_pass:
        flags.append(
            ReviewFlag(
                code="VOLTAGE_DROP_FAIL",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Appendix 4 Table 4Ab",
                message=(
                    f"Circuit {eff_spec.circuit_id} exceeds {eff_spec.vd_limit_pct:.1f}% "
                    f"voltage drop limit: ΔV={chosen.vd_pct:.2f}% ({chosen.vd_v:.2f} V)."
                ),
            )
        )

    earth_fault_method: Literal["TT_RCD", "TN_ZS"] = (
        "TT_RCD" if earthing_system == EarthingSystem.TT else "TN_ZS"
    )

    if earthing_system == EarthingSystem.TT:
        if not eff_spec.rcd_protected or eff_spec.rcd_idn_ma is None:
            flags.append(
                ReviewFlag(
                    code="TT_MISSING_RCD",
                    severity=ReviewSeverity.BLOCKER,
                    category="bs7671",
                    circuit_id=eff_spec.circuit_id,
                    regulation_ref="BS 7671 Reg 411.5.2 / 411.5.3",
                    message=(
                        f"Circuit {eff_spec.circuit_id} on TT earthing lacks mandatory RCD "
                        "protection for earth fault disconnection."
                    ),
                )
            )
        else:
            if resolved_ze_ra > eac_std.earthing.tt_electrode_stability_warning_ohm:
                flags.append(
                    ReviewFlag(
                        code="TT_ELECTRODE_UNSTABLE",
                        severity=ReviewSeverity.WARNING,
                        category="bs7671",
                        circuit_id=eff_spec.circuit_id,
                        regulation_ref="BS 7671 Reg 411.5.3 Note 2",
                        message=(
                            f"TT earth electrode resistance Ra={resolved_ze_ra:.1f} Ω exceeds "
                            f"{eac_std.earthing.tt_electrode_stability_warning_ohm:.0f} Ω "
                            "stability threshold; verify earth rod installation."
                        ),
                    )
                )
            if not chosen.earth_fault_pass and chosen.tt_touch_voltage_v is not None:
                flags.append(
                    ReviewFlag(
                        code="TT_TOUCH_VOLTAGE_EXCEEDED",
                        severity=ReviewSeverity.BLOCKER,
                        category="bs7671",
                        circuit_id=eff_spec.circuit_id,
                        regulation_ref="BS 7671 Reg 411.5.3",
                        message=(
                            f"Circuit {eff_spec.circuit_id} fails TT touch voltage limit: "
                            f"Ra·IΔn = {chosen.tt_touch_voltage_v:.2f} V > "
                            f"{eac_std.earthing.tt_touch_voltage_limit_v:.0f} V."
                        ),
                    )
                )
    elif not chosen.earth_fault_pass:
        flags.append(
            ReviewFlag(
                code="TN_ZS_EXCEEDED",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Table 41.3",
                message=(
                    f"Circuit {eff_spec.circuit_id} fails TN earth fault loop impedance: "
                    f"Zs={chosen.zs_ohm:.3f} Ω > Zs,max={zs_max_ohm:.2f} Ω "
                    f"(Type {curve.value} {breaker_in_a}A)."
                ),
            )
        )

    if not chosen.cpc_adiabatic_pass:
        flags.append(
            ReviewFlag(
                code="CPC_ADIABATIC_FAIL",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Reg 543.1.3",
                message=(
                    f"Circuit {eff_spec.circuit_id} CPC ({chosen.cpc_mm2:g} mm²) fails adiabatic "
                    f"check: minimum required {chosen.cpc_min_mm2:.2f} mm² (I²t={i2t_a2s:.0f} A²s)."
                ),
            )
        )

    if not chosen.live_adiabatic_pass:
        flags.append(
            ReviewFlag(
                code="LIVE_CONDUCTOR_ADIABATIC_FAIL",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                circuit_id=eff_spec.circuit_id,
                regulation_ref="BS 7671 Reg 434.5.2",
                message=(
                    f"Circuit {eff_spec.circuit_id} phase conductor ({chosen.cable_mm2:g} mm²) "
                    f"fails short-circuit adiabatic check: min {chosen.cpc_min_mm2:.2f} mm²."
                ),
            )
        )

    from cyelec.solver.fault_selectivity import (
        compute_circuit_fault_currents,
        lookup_vendor_catalog_ref,
    )

    assigned_phase = (
        PhaseAssignment.THREE_PHASE
        if eff_spec.is_three_phase
        else (eff_spec.fixed_phase or phase)
    )
    containment = size_conduit_or_tray(
        chosen.cable_mm2,
        is_three_phase=eff_spec.is_three_phase,
        grouping_count=eff_spec.grouping_count,
    )

    chosen_entry = cables_std.get_cable(chosen.cable_mm2)
    is_xlpe_final = eff_spec.insulation_type == "xlpe_90c"
    temp_mult_final = (
        cables_std.constants.temp_resistance_multiplier_90c
        if is_xlpe_final
        else cables_std.constants.temp_resistance_multiplier_70c
    )
    eff_path_m = (
        eff_spec.electrical_path_length_m
        if eff_spec.electrical_path_length_m is not None
        else (
            eff_spec.length_m / 4.0
            if eff_spec.topology == CircuitTopology.RING_FINAL
            else eff_spec.length_m
        )
    )
    ik1_min_a, ipe_min_a, mag_max_a, inst_pass = compute_circuit_fault_currents(
        u0_v=eac_std.system_nominal.u0_v,
        ze_or_ra_ohm=resolved_ze_ra,
        earthing_system=earthing_system,
        r1_mohm_m=chosen_entry.r20_mohm_per_m,
        r2_mohm_m=cables_std.get_r20_mohm_per_m(chosen.cpc_mm2),
        effective_length_m=eff_path_m,
        temp_mult=temp_mult_final,
        breaker_in_a=breaker_in_a,
        curve=curve,
    )
    vendor_ref = lookup_vendor_catalog_ref(
        breaker_in_a=breaker_in_a,
        curve=curve,
        rcd_protected=eff_spec.rcd_protected,
        rcd_idn_ma=eff_spec.rcd_idn_ma,
        is_three_phase=eff_spec.is_three_phase,
    )

    return SolvedCircuit(
        spec=eff_spec,
        phase=assigned_phase,
        breaker_in_a=breaker_in_a,
        breaker_curve=curve,
        device_label=device_label,
        vendor_catalog_ref=vendor_ref,
        cable_mm2=chosen.cable_mm2,
        cpc_mm2=chosen.cpc_mm2,
        electrical_path_length_m=round(eff_path_m, 2),
        worst_case_node_id=eff_spec.worst_case_node_id,
        ca=round(ca, 4),
        cg=round(cg, 4),
        ci=round(ci, 4),
        effective_derating=round(effective_derating, 4),
        it_tabulated_a=round(chosen.it_a, 2),
        iz_derated_a=round(chosen.iz_a, 2),
        required_iz_a=round(required_iz_a, 2),
        thermal_pass=chosen.thermal_pass,
        mv_per_a_per_m=round(chosen.mv_per_a_per_m, 3),
        voltage_drop_v=round(chosen.vd_v, 3),
        voltage_drop_pct=round(chosen.vd_pct, 3),
        voltage_drop_pass=chosen.vd_pass,
        r1_plus_r2_ohm=round(chosen.r1_plus_r2_ohm, 4),
        zs_ohm=round(chosen.zs_ohm, 4),
        zs_max_ohm=round(zs_max_ohm, 3),
        earth_fault_pass=chosen.earth_fault_pass,
        earth_fault_method=earth_fault_method,
        tt_touch_voltage_v=(
            round(chosen.tt_touch_voltage_v, 3) if chosen.tt_touch_voltage_v is not None else None
        ),
        adiabatic_i2t_a2s=round(i2t_a2s, 1),
        cpc_min_mm2=round(chosen.cpc_min_mm2, 3),
        cpc_adiabatic_pass=chosen.cpc_adiabatic_pass,
        live_adiabatic_pass=chosen.live_adiabatic_pass,
        ik1_min_a=ik1_min_a,
        ipe_min_a=ipe_min_a,
        magnetic_trip_max_a=mag_max_a,
        instantaneous_trip_pass=inst_pass,
        recommended_containment=containment,
        overall_pass=chosen.all_pass,
        upsize_reasons=upsize_reasons,
        flags=flags,
    )
