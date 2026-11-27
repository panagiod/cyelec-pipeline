"""Electricity Authority of Cyprus (EAC) supply selection and compliance escalation solver."""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.building import BuildingModel, BuildingType, RoomType
from cyelec.models.electrical import (
    EacComplianceResult,
    EacProfile,
    PhaseBalanceResult,
    ReviewFlag,
    ReviewSeverity,
    SolvedCircuit,
)
from cyelec.standards_loader import StandardsBundle, SupplyTier


def _select_supply_tier(
    tiers: list[SupplyTier], demand_a: float
) -> tuple[SupplyTier, bool, SupplyTier | None]:
    ordered = sorted(tiers, key=lambda t: t.amps)
    for idx, tier in enumerate(ordered):
        if float(tier.amps) >= demand_a - 1e-9:
            next_tier = ordered[idx + 1] if idx + 1 < len(ordered) else None
            return tier, False, next_tier
    return ordered[-1], True, None


def evaluate_eac_compliance(
    building: BuildingModel,
    circuits: list[SolvedCircuit],
    standards: StandardsBundle,
    *,
    eac_profile: EacProfile | None = None,
    phase_balance: PhaseBalanceResult | None = None,
    pv_capacity_kva_override: float | None = None,
    prospective_fault_current_ka: float | None = None,
    min_supply_headroom_pct: float = 0.0,
    min_main_breaker_a: int | None = None,
) -> EacComplianceResult:
    """Evaluate Cyprus EAC 1P/3P supply rules, tiering, CT metering, and BS 7671+A2 checks."""
    eac_rules = standards.eac_rules
    active_profile = eac_profile or eac_rules.default_profile
    profile_spec = eac_rules.profiles[active_profile]
    supply_rules = eac_rules.supply_rules

    pv_kva = (
        pv_capacity_kva_override
        if pv_capacity_kva_override is not None
        else building.pv_capacity_kva
    )

    total_connected_kva = sum(
        c.spec.connected_load_w / (1000.0 * c.spec.power_factor) for c in circuits
    )
    total_diversified_kva = sum(
        c.spec.diversified_load_w / (1000.0 * c.spec.power_factor) for c in circuits
    )

    three_phase_reasons: list[str] = []
    if building.building_type in supply_rules.mandatory_three_phase_building_types:
        three_phase_reasons.append(
            f"Building type '{building.building_type.value}' requires 3-phase "
            "supply under EAC rules."
        )
    if pv_kva > supply_rules.pv_three_phase_threshold_kva:
        three_phase_reasons.append(
            f"PV generation capacity ({pv_kva:.2f} kVA) exceeds EAC single-phase limit "
            f"({supply_rules.pv_three_phase_threshold_kva:.1f} kVA)."
        )
    if total_diversified_kva > supply_rules.single_phase_max_kva:
        three_phase_reasons.append(
            f"Diversified demand ({total_diversified_kva:.2f} kVA) exceeds EAC single-phase "
            f"maximum ({supply_rules.single_phase_max_kva:.1f} kVA)."
        )
    if any(c.spec.is_three_phase for c in circuits):
        three_phase_reasons.append("Installation includes one or more 3-phase final circuits.")

    mandatory_three_phase = len(three_phase_reasons) > 0
    supply_phases: Literal[1, 3] = 3 if mandatory_three_phase else 1

    tier_table = (
        eac_rules.supply_tiers.three_phase
        if supply_phases == 3
        else eac_rules.supply_tiers.single_phase
    )
    if supply_phases == 3:
        if phase_balance is not None:
            max_phase_demand_a = phase_balance.max_phase_current_a
        else:
            max_phase_demand_a = (
                (total_diversified_kva * 1000.0)
                / (math.sqrt(3.0) * eac_rules.system_nominal.ul_v)
            )
    else:
        max_phase_demand_a = sum(c.spec.design_current_ib_a for c in circuits)

    raw_tier, _, _ = _select_supply_tier(tier_table, max_phase_demand_a)
    sizing_demand_a = max_phase_demand_a
    if min_supply_headroom_pct > 0.0:
        sizing_demand_a = max_phase_demand_a / max(1.0 - (min_supply_headroom_pct / 100.0), 0.5)
    if min_main_breaker_a is not None:
        sizing_demand_a = max(sizing_demand_a, float(min_main_breaker_a))

    selected_tier, exceeded_max_tier, next_tier = _select_supply_tier(tier_table, sizing_demand_a)
    utilization_pct = round((max_phase_demand_a / max(float(selected_tier.amps), 1.0)) * 100.0, 1)

    ct_metering_required = (
        max(max_phase_demand_a, float(selected_tier.amps if min_main_breaker_a else 0.0))
        > supply_rules.ct_metering_threshold_a_per_phase
    )
    substation_warning = total_diversified_kva >= supply_rules.substation_warning_kva
    substation_consultation_required = total_diversified_kva >= supply_rules.substation_blocker_kva

    flags: list[ReviewFlag] = [
        ReviewFlag(
            code="EAC_PROFILE_ACTIVE",
            severity=ReviewSeverity.INFO,
            category="eac",
            regulation_ref=profile_spec.standard_ref,
            message=(
                f"Active Cyprus EAC profile: {active_profile.value} "
                f"(bathroom socket exclusion {profile_spec.bathroom_socket_exclusion_m:.1f} m; "
                f"recommended supply {selected_tier.label}, utilization {utilization_pct:.1f}%)."
            ),
            requires_etek_signoff=False,
        )
    ]

    if selected_tier.amps > raw_tier.amps and (
        min_supply_headroom_pct > 0.0 or min_main_breaker_a is not None
    ):
        flags.append(
            ReviewFlag(
                code="EAC_SUPPLY_HEADROOM_UPGRADED",
                severity=ReviewSeverity.INFO,
                category="eac",
                regulation_ref="EAC Standard LV Supply Tiers / Future-Ready Margin",
                message=(
                    f"Supply tier upgraded from {raw_tier.label} to {selected_tier.label} "
                    f"(utilization {utilization_pct:.1f}%) per engineer headroom/breaker setting."
                ),
                requires_etek_signoff=False,
            )
        )

    if exceeded_max_tier:
        flags.append(
            ReviewFlag(
                code="EAC_EXCEEDS_MAX_LV_TIER",
                severity=ReviewSeverity.BLOCKER,
                category="eac",
                regulation_ref="EAC Standard LV Supply Tiers",
                message=(
                    f"Peak phase demand {max_phase_demand_a:.1f} A exceeds largest tabulated "
                    f"EAC LV tier ({selected_tier.label}); dedicated MV supply design required."
                ),
            )
        )
    elif next_tier is not None and utilization_pct >= 92.0:
        spare_pct = max(0.0, round(100.0 - utilization_pct, 1))
        flags.append(
            ReviewFlag(
                code="EAC_SUPPLY_LOW_HEADROOM",
                severity=ReviewSeverity.WARNING,
                category="eac",
                regulation_ref="EAC Supply Capacity Planning / BS 7671 Reg 311.1",
                message=(
                    f"Diversified load ({total_diversified_kva:.2f} kVA, "
                    f"{max_phase_demand_a:.1f} A/ph) utilizes {utilization_pct:.1f}% of "
                    f"{selected_tier.label} ({spare_pct:.1f}% spare). Strongly consider "
                    f"upgrading to {next_tier.label} for future EV/PV/HVAC expansion."
                ),
                requires_etek_signoff=True,
            )
        )
    elif next_tier is not None and utilization_pct >= 85.0:
        spare_pct = max(0.0, round(100.0 - utilization_pct, 1))
        flags.append(
            ReviewFlag(
                code="EAC_SUPPLY_MODERATE_HEADROOM",
                severity=ReviewSeverity.INFO,
                category="eac",
                regulation_ref="EAC Supply Capacity Planning / BS 7671 Reg 311.1",
                message=(
                    f"Supply tier {selected_tier.label} is at {utilization_pct:.1f}% utilization "
                    f"({spare_pct:.1f}% spare). Next EAC tier is {next_tier.label}."
                ),
                requires_etek_signoff=False,
            )
        )

    if pv_kva > supply_rules.pv_three_phase_threshold_kva:
        flags.append(
            ReviewFlag(
                code="EAC_PV_THREE_PHASE_MANDATORY",
                severity=ReviewSeverity.INFO,
                category="eac",
                regulation_ref="EAC Net-Metering / PV Connection Rules",
                message=(
                    f"PV capacity {pv_kva:.2f} kVA > 5.0 kVA mandates a 3-phase EAC supply "
                    "and grid-tie inverter compliance submission."
                ),
            )
        )

    # BS 7671:2018+A2:2022 Reg 530.3.201 (bidirectional RCBO) & Reg 551.7.2 (busbar backfeed)
    if pv_kva > 0.0:
        pv_i_a = (
            (pv_kva * 1000.0) / (math.sqrt(3.0) * eac_rules.system_nominal.ul_v)
            if supply_phases == 3
            else (pv_kva * 1000.0) / eac_rules.system_nominal.u0_v
        )
        combined_busbar_a = selected_tier.amps + pv_i_a
        busbar_warn = combined_busbar_a > 100.0 and not ct_metering_required
        flags.append(
            ReviewFlag(
                code="BS7671_PV_BIDIRECTIONAL_AND_BUSBAR_CHECK",
                severity=ReviewSeverity.WARNING if busbar_warn else ReviewSeverity.INFO,
                category="bs7671",
                regulation_ref="BS 7671:2018+A2 Reg 530.3.201 & Reg 551.7.2",
                message=(
                    f"PV backfeed ({pv_kva:.1f} kVA = {pv_i_a:.1f} A/phase): specify a "
                    "bidirectional RCBO/RCD on the PV circuit and verify DB busbar rating >= "
                    f"In + Ipv = {combined_busbar_a:.1f} A."
                ),
            )
        )

    if ct_metering_required:
        flags.append(
            ReviewFlag(
                code="EAC_CT_METERING_REQUIRED",
                severity=ReviewSeverity.WARNING,
                category="eac",
                regulation_ref="EAC Metering Specification (>100 A/phase)",
                message=(
                    f"Peak phase demand {max_phase_demand_a:.1f} A exceeds 100 A/phase; "
                    "EAC Current Transformer (CT) metering chamber and panel required."
                ),
            )
        )

    # Prospective Fault Current (Ipf) Breaking Capacity Check (BS 7671 Reg 434.5.1)
    is_commercial = building.building_type in (
        BuildingType.OFFICE,
        BuildingType.SHOP,
        BuildingType.WORKSHOP,
        BuildingType.INDUSTRIAL,
    )
    eff_ipf_ka = (
        prospective_fault_current_ka
        if prospective_fault_current_ka is not None
        else (10.0 if (ct_metering_required or is_commercial) else 6.0)
    )
    if eff_ipf_ka > 10.0:
        flags.append(
            ReviewFlag(
                code="BS7671_IPF_EXCEEDS_10KA",
                severity=ReviewSeverity.BLOCKER,
                category="bs7671",
                regulation_ref="BS 7671 Reg 434.5.1",
                message=(
                    f"Prospective fault current Ipf={eff_ipf_ka:.1f} kA exceeds standard 10 kA "
                    "MCB/RCBO breaking capacity; specify MCCB / BS 88 HRC back-up protection."
                ),
            )
        )
    elif eff_ipf_ka > 6.0 or ct_metering_required:
        flags.append(
            ReviewFlag(
                code="BS7671_IPF_10KA_SWITCHGEAR_REQUIRED",
                severity=ReviewSeverity.WARNING,
                category="bs7671",
                regulation_ref="BS 7671 Reg 434.5.1 (BS EN 60898 / 61009)",
                message=(
                    f"Site Ipf={eff_ipf_ka:.1f} kA (>6 kA domestic rating): specify minimum "
                    "10 kA breaking-capacity MCB/RCBO switchgear throughout MDB/sub-boards."
                ),
            )
        )

    # BS 7671:2018+A2 Reg 421.1.7 AFDD advisory for residential sleeping accommodation
    has_sleeping = any(
        r.normalized_type in (RoomType.BEDROOM, RoomType.MASTER_BEDROOM) for r in building.rooms
    )
    if active_profile == EacProfile.EAC_18TH_A2 and has_sleeping:
        flags.append(
            ReviewFlag(
                code="BS7671_AFDD_ADVISORY_421_1_7",
                severity=ReviewSeverity.INFO,
                category="bs7671",
                regulation_ref="BS 7671:2018+A2 Reg 421.1.7",
                message=(
                    "Sleeping accommodation detected under 18th+A2 profile: AFDDs are mandatory "
                    "on AC socket final circuits in HRRBs (>=18 m), HMOs, and care homes, and "
                    "recommended for residential bedroom socket circuits."
                ),
                requires_etek_signoff=False,
            )
        )

    if substation_consultation_required:
        flags.append(
            ReviewFlag(
                code="EAC_SUBSTATION_CONSULTATION_BLOCKER",
                severity=ReviewSeverity.BLOCKER,
                category="eac",
                regulation_ref="EAC Distribution Network Planning (>=150 kVA)",
                message=(
                    f"Diversified demand {total_diversified_kva:.1f} kVA >= 150 kVA; "
                    "formal EAC substation consultation and dedicated MV/LV transformer room "
                    "design approval are mandatory prior to plan submission."
                ),
            )
        )
    elif substation_warning:
        flags.append(
            ReviewFlag(
                code="EAC_SUBSTATION_WARNING",
                severity=ReviewSeverity.WARNING,
                category="eac",
                regulation_ref="EAC Distribution Network Planning (>=100 kVA)",
                message=(
                    f"Diversified demand {total_diversified_kva:.1f} kVA >= 100 kVA; "
                    "early EAC network capacity inquiry and potential substation "
                    "allocation advised."
                ),
            )
        )

    return EacComplianceResult(
        eac_profile=active_profile,
        supply_phases=supply_phases,
        mandatory_three_phase=mandatory_three_phase,
        three_phase_reasons=three_phase_reasons,
        total_connected_kva=round(total_connected_kva, 2),
        total_diversified_kva=round(total_diversified_kva, 2),
        max_phase_demand_a=round(max_phase_demand_a, 2),
        recommended_supply_tier=selected_tier.label,
        recommended_main_breaker_a=selected_tier.amps,
        supply_tier_utilization_pct=utilization_pct,
        next_supply_tier=next_tier.label if next_tier is not None else None,
        ct_metering_required=ct_metering_required,
        substation_warning=substation_warning,
        substation_consultation_required=substation_consultation_required,
        bathroom_socket_exclusion_m=profile_spec.bathroom_socket_exclusion_m,
        flags=flags,
    )
