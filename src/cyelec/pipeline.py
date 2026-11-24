"""Stateless end-to-end Phase 1 electrical engineering pipeline orchestrator."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from cyelec import __version__
from cyelec.models.building import BuildingModel
from cyelec.models.electrical import (
    CircuitSpec,
    DesignOverrides,
    EacProfile,
    EarthingSystem,
    ElectricalDesignResult,
    PhaseAssignment,
    PhaseBalanceResult,
    ReviewFlag,
    ReviewSeverity,
    RunProvenance,
    SolvedCircuit,
)
from cyelec.render.calc_report import export_calc_reports
from cyelec.render.schedule_xlsx import export_load_schedule_csv, export_load_schedule_xlsx
from cyelec.solver.balancer import balance_three_phase_loads
from cyelec.solver.bs7671 import _apply_circuit_override, solve_circuit
from cyelec.solver.eac_compliance import evaluate_eac_compliance
from cyelec.solver.grouper import generate_circuits
from cyelec.standards_loader import StandardsBundle, load_standards


def _compute_input_sha256(building: BuildingModel) -> str:
    if building.source_sha256:
        return building.source_sha256
    canonical = building.model_dump_json(exclude_none=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _requires_three_phase_supply(
    building: BuildingModel,
    specs: list[CircuitSpec],
    standards: StandardsBundle,
    pv_kva: float,
) -> bool:
    sr = standards.eac_rules.supply_rules
    if building.building_type in sr.mandatory_three_phase_building_types:
        return True
    if pv_kva > sr.pv_three_phase_threshold_kva:
        return True
    total_div_kva = sum(s.diversified_load_w / (1000.0 * s.power_factor) for s in specs)
    if total_div_kva > sr.single_phase_max_kva:
        return True
    return any(s.is_three_phase for s in specs)


def run_phase1_pipeline(
    building: BuildingModel,
    *,
    overrides: DesignOverrides | None = None,
    standards_dir: Path | None = None,
) -> ElectricalDesignResult:
    """Execute stateless Phase 1 pipeline: grouping, MILP balancing, BS 7671 & EAC checks."""
    standards = load_standards(standards_dir)
    eac_rules = standards.eac_rules

    eac_profile: EacProfile = (
        overrides.eac_profile
        if (overrides is not None and overrides.eac_profile is not None)
        else eac_rules.default_profile
    )
    earthing_system: EarthingSystem = (
        overrides.earthing_system
        if (overrides is not None and overrides.earthing_system is not None)
        else eac_rules.earthing.default_system
    )
    ze_or_ra_ohm: float = (
        overrides.ze_or_ra_ohm
        if (overrides is not None and overrides.ze_or_ra_ohm is not None)
        else eac_rules.earthing.default_ze_or_ra_ohm[earthing_system]
    )
    pv_kva: float = (
        overrides.pv_capacity_kva
        if (overrides is not None and overrides.pv_capacity_kva is not None)
        else building.pv_capacity_kva
    )

    effective_building = (
        building.model_copy(update={"pv_capacity_kva": pv_kva})
        if pv_kva != building.pv_capacity_kva
        else building
    )

    # 1. Deterministic circuit grouping
    raw_specs = generate_circuits(effective_building, standards, overrides=overrides)

    # Pre-apply per-circuit overrides (e.g., diversity/fixed_phase) before MILP balancing
    circuit_overrides = overrides.circuit_overrides if overrides is not None else {}
    effective_specs = [
        _apply_circuit_override(spec, circuit_overrides.get(spec.circuit_id))
        for spec in raw_specs
    ]

    # 2. Exact MILP 3-phase balancing (when supply is 3-phase)
    is_three_phase_supply = _requires_three_phase_supply(
        effective_building, effective_specs, standards, pv_kva
    )
    phase_balance: PhaseBalanceResult | None = None
    if is_three_phase_supply:
        phase_balance = balance_three_phase_loads(
            effective_specs,
            imbalance_target_pct=eac_rules.supply_rules.phase_imbalance_target_pct,
        )

    # 3. BS 7671 deterministic sizing & compliance verification for each circuit
    solved_circuits: list[SolvedCircuit] = []
    for raw_spec in raw_specs:
        ov = circuit_overrides.get(raw_spec.circuit_id)
        if phase_balance is not None:
            assigned_phase = phase_balance.assignments[raw_spec.circuit_id]
        else:
            assigned_phase = (
                PhaseAssignment.THREE_PHASE if raw_spec.is_three_phase else PhaseAssignment.L1
            )

        solved = solve_circuit(
            raw_spec,
            standards,
            earthing_system=earthing_system,
            ze_or_ra_ohm=ze_or_ra_ohm,
            phase=assigned_phase,
            override=ov,
        )
        solved_circuits.append(solved)

    # 4. Cyprus EAC supply & escalation checks
    eac_compliance = evaluate_eac_compliance(
        effective_building,
        solved_circuits,
        standards,
        eac_profile=eac_profile,
        phase_balance=phase_balance,
        pv_capacity_kva_override=pv_kva,
    )

    # 5. Aggregate statutory ETEK Human Review Flags Register
    review_flags: list[ReviewFlag] = [
        ReviewFlag(
            code="ETEK_SIGN_OFF_REQUIRED",
            severity=ReviewSeverity.INFO,
            category="governance",
            regulation_ref="Cyprus ETEK Statutory Engineering Practice & EAC Plan Approval",
            message=(
                "Automated deterministic design output requires formal review, verification, "
                "and stamp by a licensed ETEK Electrical Engineer before EAC submission."
            ),
            requires_etek_signoff=True,
        )
    ]
    review_flags.extend(eac_compliance.flags)
    if phase_balance is not None:
        review_flags.extend(phase_balance.flags)
    for circ in solved_circuits:
        review_flags.extend(circ.flags)

    has_blockers = any(f.severity == ReviewSeverity.BLOCKER for f in review_flags)

    provenance = RunProvenance(
        pipeline_version=__version__,
        standards_versions=standards.versions_map(),
        input_sha256=_compute_input_sha256(effective_building),
        eac_profile=eac_profile,
        earthing_system=earthing_system,
        timestamp_utc=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        overrides_applied=overrides is not None,
    )

    return ElectricalDesignResult(
        provenance=provenance,
        building=effective_building,
        earthing_system=earthing_system,
        ze_or_ra_ohm=ze_or_ra_ohm,
        circuits=solved_circuits,
        phase_balance=phase_balance,
        eac_compliance=eac_compliance,
        review_flags=review_flags,
        has_blockers=has_blockers,
        etek_signoff_required=True,
    )


def export_phase1_deliverables(
    result: ElectricalDesignResult,
    out_dir: Path,
) -> dict[str, Path]:
    """Write JSON snapshot, formula-backed Excel, CSV, Markdown, and A4-landscape HTML outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "design_result.json"
    xlsx_path = out_dir / "load_schedule.xlsx"
    csv_path = out_dir / "load_schedule.csv"
    md_path = out_dir / "calc_report.md"
    html_path = out_dir / "calc_report.html"

    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    export_load_schedule_xlsx(result, xlsx_path)
    export_load_schedule_csv(result, csv_path)
    export_calc_reports(result, md_path, html_path)

    return {
        "json": json_path,
        "xlsx": xlsx_path,
        "csv": csv_path,
        "md": md_path,
        "html": html_path,
    }
