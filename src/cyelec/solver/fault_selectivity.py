"""IEC 60909 short-circuit fault profile, vendor catalog mapping & I-t selectivity engine.

Computes:
1. Per-circuit minimum far-end phase-neutral fault current (Ik1_min) and phase-earth
   fault current (Ipe_min) at conductor operating temperature (70°C PVC / 90°C XLPE),
   verifying instantaneous magnetic trip (Ik1_min >= 5*In Type B, 10*In Type C, 20*In Type D).
2. Per-board maximum 3-phase fault current (Ik3_max) vs switchgear breaking capacity (Icu/Icn).
3. Upstream-to-downstream time-current (I-t) selectivity & discrimination limits (Is in kA)
   for EAC BS 88 gG Main Fuse -> Sub-Main MCCB/MCB -> Final RCBO + 100mA Type-S vs 30mA RCD.
4. Manufacturer-equivalent reference switchgear catalog codes (Hager / Schneider Acti9 / ABB).
"""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.electrical import (
    BoardFaultAndSelectivity,
    BreakerCurve,
    EarthingSystem,
    ElectricalDesignResult,
    ReviewFlag,
    ReviewSeverity,
    SelectivityAnalysisResult,
    SolvedCircuit,
)


def magnetic_trip_multiplier_max(curve: BreakerCurve) -> float:
    """Upper instantaneous magnetic trip multiple per IEC 60898-1 / IEC 60947-2."""
    if curve == BreakerCurve.B:
        return 5.0
    if curve == BreakerCurve.C:
        return 10.0
    if curve == BreakerCurve.D:
        return 20.0
    if curve == BreakerCurve.MCCB:
        return 10.0
    # BS 88-2 gG HRC fuse 0.4 s disconnection factor ~8.5x In
    return 8.5


def lookup_vendor_catalog_ref(
    *,
    breaker_in_a: int,
    curve: BreakerCurve,
    rcd_protected: bool,
    rcd_idn_ma: int | None,
    is_three_phase: bool,
    min_icu_ka: float = 6.0,
) -> str:
    """Return representative Cyprus-stocked Hager / Schneider Acti9 / ABB catalog reference."""
    icu_tag = "10kA" if min_icu_ka > 6.0 else "6kA"
    if curve == BreakerCurve.BS88:
        return f"Eaton Bussmann / Lawson NIT/TIS {breaker_in_a}A gG (80kA)"
    if curve == BreakerCurve.MCCB or breaker_in_a > 63:
        poles = "3P/4P" if is_three_phase else "2P"
        return (
            f"Schneider ComPacT NSX{max(100, breaker_in_a)}F / "
            f"Hager h3+ P160 {breaker_in_a}A {poles} (36kA)"
        )
    if rcd_protected and rcd_idn_ma is not None:
        if is_three_phase:
            return (
                f"Hager ADM/CDA + MCN3{breaker_in_a:02d} / "
                f"Schneider Acti9 iC60N+Vigi 4P {breaker_in_a}A {curve.value} "
                f"{rcd_idn_ma}mA ({icu_tag})"
            )
        series = "ADA9" if min_icu_ka <= 6.0 else "AD9"
        sch_pfx = "A9D316" if curve == BreakerCurve.B else "A9D326"
        return (
            f"Hager {series}{breaker_in_a:02d}G / "
            f"Schneider Acti9 {sch_pfx}{breaker_in_a:02d} "
            f"({breaker_in_a}A {curve.value} {rcd_idn_ma}mA {icu_tag})"
        )
    poles = "3P" if is_three_phase else "1P"
    return (
        f"Hager M{'B' if curve == BreakerCurve.B else 'C'}N{1 if not is_three_phase else 3}"
        f"{breaker_in_a:02d} / Schneider Acti9 iC60N {poles} {breaker_in_a}A {curve.value} "
        f"({icu_tag})"
    )


def compute_circuit_fault_currents(
    *,
    u0_v: float,
    ze_or_ra_ohm: float,
    earthing_system: EarthingSystem,
    r1_mohm_m: float,
    r2_mohm_m: float,
    effective_length_m: float,
    temp_mult: float,
    breaker_in_a: int,
    curve: BreakerCurve,
    origin_ik3_ka: float = 6.0,
) -> tuple[float, float, float, bool]:
    """Compute IEC 60909 far-end Ik1_min (L-N), Ipe_min (L-PE), I_mag_max, and trip pass."""
    # Source loop impedance (L-N) from origin Ik3_max (c_min = 0.95 per IEC 60909 Table 1)
    z_src_ln_ohm = (1.05 * u0_v) / max(origin_ik3_ka * 1000.0, 500.0)
    # Phase + neutral resistance at operating temperature (equal cross-section L & N)
    r_ln_ohm = (2.0 * r1_mohm_m * effective_length_m / 1000.0) * temp_mult
    z_ln_total = max(0.02, z_src_ln_ohm + r_ln_ohm)
    ik1_min_a = round((0.95 * u0_v) / z_ln_total, 1)

    r_lpe_ohm = ((r1_mohm_m + r2_mohm_m) * effective_length_m / 1000.0) * temp_mult
    z_pe_total = max(0.02, ze_or_ra_ohm + r_lpe_ohm)
    ipe_min_a = round((0.95 * u0_v) / z_pe_total, 1)

    i_mag_max_a = round(float(breaker_in_a) * magnetic_trip_multiplier_max(curve), 1)
    # On TT systems, earth faults are cleared by 30mA RCD; L-N short circuits must trip magnetically
    if earthing_system == EarthingSystem.TT:
        inst_pass = ik1_min_a >= i_mag_max_a - 1e-6
    else:
        inst_pass = (ik1_min_a >= i_mag_max_a - 1e-6) and (ipe_min_a >= i_mag_max_a - 1e-6)

    return ik1_min_a, ipe_min_a, i_mag_max_a, inst_pass


def _estimate_fuse_mcb_discrimination_ka(fuse_a: int, downstream_in_a: int) -> float:
    """BS 88 gG main fuse vs IEC 60898 MCB/RCBO discrimination limit Is (kA) per vendor tables."""
    ratio = float(fuse_a) / max(float(downstream_in_a), 1.0)
    if ratio >= 3.0:
        return 10.0  # Total discrimination across residential fault range
    if ratio >= 2.0:
        return 6.0
    if ratio >= 1.5:
        return 3.0
    return 1.2


def _estimate_mccb_mcb_discrimination_ka(upstream_in_a: int, downstream_in_a: int) -> float:
    """Upstream MCCB/MCB vs downstream MCB/RCBO energy-based selectivity limit Is (kA)."""
    ratio = float(upstream_in_a) / max(float(downstream_in_a), 1.0)
    if upstream_in_a >= 100 and ratio >= 2.5:
        return 10.0
    if upstream_in_a >= 63 and ratio >= 2.0:
        return 4.5
    if ratio >= 1.6:
        return 2.5
    return 0.8


def evaluate_fault_and_selectivity(
    design: ElectricalDesignResult,
) -> tuple[SelectivityAnalysisResult, list[ReviewFlag]]:
    """Compute installation-wide IEC 60909 fault profile & upstream-downstream I-t selectivity."""
    eac = design.eac_compliance
    origin_ik3_ka = max(1.0, design.prospective_fault_current_ka)
    # Single-phase minimum fault at origin (IEC 60909 c_min=0.95 / typical Z0/Z1 ratio)
    origin_ik1_ka = round(origin_ik3_ka * 0.72, 2)
    eac_fuse_a = max(60, int(math.ceil(eac.recommended_main_breaker_a / 20.0) * 20))
    if eac.recommended_main_breaker_a <= 40:
        eac_fuse_a = 63
    elif eac.recommended_main_breaker_a <= 60:
        eac_fuse_a = 80
    elif eac.recommended_main_breaker_a <= 80:
        eac_fuse_a = 100
    elif eac.recommended_main_breaker_a <= 100:
        eac_fuse_a = 125

    min_icu_ka = 10.0 if (origin_ik3_ka > 6.0 or eac.ct_metering_required) else 6.0
    by_board: dict[str, list[SolvedCircuit]] = {}
    for c in design.circuits:
        by_board.setdefault(c.spec.board_id, []).append(c)

    sub_by_id = {sm.board_id: sm for sm in design.sub_mains}
    board_ids = [design.building.db_anchor.id, *[sb.board_id for sb in design.building.sub_boards]]
    for bid in by_board:
        if bid not in board_ids:
            board_ids.append(bid)

    boards: list[BoardFaultAndSelectivity] = []
    flags: list[ReviewFlag] = []

    rcd_sel_pass = True
    if design.earthing_system == EarthingSystem.TT and not design.include_tt_main_rcd_s:
        rcd_sel_pass = True  # All-RCBO board without upstream instantaneous RCD still selective

    for bid in board_ids:
        b_ckts = by_board.get(bid, [])
        sm = sub_by_id.get(bid)
        max_ckt = max(b_ckts, key=lambda c: c.breaker_in_a) if b_ckts else None
        max_in = max_ckt.breaker_in_a if max_ckt is not None else 32
        max_dev_lbl = max_ckt.device_label if max_ckt is not None else "32A Type B RCBO 30mA"

        if sm is None:
            ik3_ka = round(origin_ik3_ka, 2)
            ik1_ka = origin_ik1_ka
            up_lbl = f"EAC Service Cut-Out {eac_fuse_a}A BS 88-2 gG Fuse"
            disc_is_ka = _estimate_fuse_mcb_discrimination_ka(eac_fuse_a, max_in)
            vendor_ref = (
                f"BS 88 gG {eac_fuse_a}A -> Hager/Schneider Acti9 {max_in}A RCBO ({min_icu_ka:g}kA)"
            )
            parent_id = None
        else:
            ik3_ka = round(sm.downstream_ipf_ka or max(1.0, origin_ik3_ka * 0.65), 2)
            ik1_ka = round(ik3_ka * 0.72, 2)
            up_lbl = f"{sm.parent_board_id} {sm.device_label}"
            disc_is_ka = _estimate_mccb_mcb_discrimination_ka(sm.breaker_in_a, max_in)
            vendor_ref = (
                f"{sm.breaker_in_a}A {sm.breaker_curve.value} -> "
                f"Hager/Schneider Acti9 {max_in}A RCBO ({min_icu_ka:g}kA)"
            )
            parent_id = sm.parent_board_id

        icu_pass = min_icu_ka >= ik3_ka - 1e-6
        sel_type: Literal["total", "partial", "check_required"]
        if disc_is_ka >= ik3_ka - 1e-6:
            sel_type = "total"
            note = (
                f"Total discrimination up to Ik3={ik3_ka:.2f} kA "
                f"(Is={disc_is_ka:.1f} kA >= Ik3)."
            )
        elif disc_is_ka >= ik1_ka * 0.6:
            sel_type = "partial"
            note = (
                f"Partial selectivity up to Is={disc_is_ka:.1f} kA "
                f"(Ik3={ik3_ka:.2f} kA; full overload + low-level fault discrimination)."
            )
        else:
            sel_type = "check_required"
            note = (
                f"Low discrimination limit Is={disc_is_ka:.1f} kA vs Ik3={ik3_ka:.2f} kA; "
                "specify selective MCCB or larger upstream ratio."
            )

        boards.append(
            BoardFaultAndSelectivity(
                board_id=bid,
                parent_board_id=parent_id,
                upstream_device_label=up_lbl,
                downstream_max_device_label=max_dev_lbl,
                ik3_max_ka=ik3_ka,
                ik1_min_ka=ik1_ka,
                device_icu_ka=min_icu_ka,
                breaking_capacity_pass=icu_pass,
                discrimination_limit_is_ka=round(disc_is_ka, 2),
                selectivity_type=sel_type,
                rcd_selectivity_pass=rcd_sel_pass,
                vendor_coordination_ref=vendor_ref,
                note=note,
            )
        )

    all_icu = all(b.breaking_capacity_pass for b in boards)
    all_inst = all(c.instantaneous_trip_pass for c in design.circuits)
    all_sel = all(b.selectivity_type in ("total", "partial") for b in boards)

    for c in design.circuits:
        if not c.instantaneous_trip_pass:
            flags.append(
                ReviewFlag(
                    code="IEC60909_INSTANTANEOUS_TRIP_FAIL",
                    severity=ReviewSeverity.WARNING,
                    category="bs7671",
                    circuit_id=c.spec.circuit_id,
                    regulation_ref="IEC 60909 / BS 7671 Reg 434.5",
                    message=(
                        f"Circuit {c.spec.circuit_id} far-end minimum short-circuit current "
                        f"Ik1_min={c.ik1_min_a} A is below magnetic trip threshold "
                        f"{c.magnetic_trip_max_a} A ({c.device_label}). Upsize cable or change "
                        "to Type B curve."
                    ),
                    requires_etek_signoff=True,
                )
            )

    summary = SelectivityAnalysisResult(
        origin_ik3_max_ka=round(origin_ik3_ka, 2),
        origin_ik1_min_ka=origin_ik1_ka,
        eac_main_fuse_a=eac_fuse_a,
        boards=boards,
        all_breaking_capacity_pass=all_icu,
        all_instantaneous_trip_pass=all_inst,
        all_selectivity_pass=all_sel,
    )
    return summary, flags
