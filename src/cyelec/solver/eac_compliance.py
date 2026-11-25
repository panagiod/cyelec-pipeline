"""Electricity Authority of Cyprus (EAC) supply selection and compliance escalation solver."""

from __future__ import annotations

import math
from typing import Literal

from cyelec.models.building import BuildingModel
from cyelec.models.electrical import (
    EacComplianceResult,
    EacProfile,
    PhaseBalanceResult,
    ReviewFlag,
    ReviewSeverity,
    SolvedCircuit,
)
from cyelec.standards_loader import StandardsBundle, SupplyTier


def _select_supply_tier(tiers: list[SupplyTier], demand_a: float) -> tuple[SupplyTier, bool]:
    ordered = sorted(tiers, key=lambda t: t.amps)
    for tier in ordered:
        if float(tier.amps) >= demand_a - 1e-9:
            return tier, False
    return ordered[-1], True


def evaluate_eac_compliance(
    building: BuildingModel,
    circuits: list[SolvedCircuit],
    standards: StandardsBundle,
    *,
    eac_profile: EacProfile | None = None,
    phase_balance: PhaseBalanceResult | None = None,
    pv_capacity_kva_override: float | None = None,
) -> EacComplianceResult:
    """Evaluate Cyprus EAC 1P/3P supply rules, tiering, CT metering, and substation escalations."""
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

    if supply_phases == 3:
        if phase_balance is not None:
            max_phase_demand_a = phase_balance.max_phase_current_a
        else:
            max_phase_demand_a = (
                (total_diversified_kva * 1000.0)
                / (math.sqrt(3.0) * eac_rules.system_nominal.ul_v)
            )
        selected_tier, exceeded_max_tier = _select_supply_tier(
            eac_rules.supply_tiers.three_phase, max_phase_demand_a
        )
    else:
        max_phase_demand_a = sum(c.spec.design_current_ib_a for c in circuits)
        selected_tier, exceeded_max_tier = _select_supply_tier(
            eac_rules.supply_tiers.single_phase, max_phase_demand_a
        )

    ct_metering_required = max_phase_demand_a > supply_rules.ct_metering_threshold_a_per_phase
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
                f"recommended supply {selected_tier.label})."
            ),
            requires_etek_signoff=False,
        )
    ]

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
        ct_metering_required=ct_metering_required,
        substation_warning=substation_warning,
        substation_consultation_required=substation_consultation_required,
        bathroom_socket_exclusion_m=profile_spec.bathroom_socket_exclusion_m,
        flags=flags,
    )
