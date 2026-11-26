"""Stateless end-to-end Phase 1 & Phase 2 electrical engineering pipeline orchestrator."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cyelec import __version__
from cyelec.ingest.dwg_bridge import convert_dwg_to_dxf
from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.ingest.ifc_ingest import ingest_ifc
from cyelec.ingest.room_classifier import RoomClassifierFallback
from cyelec.models.building import BuildingModel, BuildingType, DxfLayerMapping, RoomType
from cyelec.models.electrical import (
    CircuitSpec,
    DesignOverrides,
    EacProfile,
    EarthingSystem,
    ElectricalDesignResult,
    PhaseAssignment,
    PhaseBalanceResult,
    ReportLanguage,
    ReviewFlag,
    ReviewSeverity,
    RunProvenance,
    SolvedCircuit,
    SolvedSubMain,
)
from cyelec.models.spatial import Phase2DesignResult, SpatialLayoutResult
from cyelec.render.calc_report import export_calc_reports
from cyelec.render.layout_dxf import export_electrical_layout_dxf
from cyelec.render.schedule_xlsx import (
    export_boq_csv,
    export_load_schedule_csv,
    export_load_schedule_xlsx,
)
from cyelec.solver.balancer import balance_three_phase_loads
from cyelec.solver.boq import generate_boq
from cyelec.solver.bs7671 import _apply_circuit_override, solve_circuit
from cyelec.solver.eac_compliance import evaluate_eac_compliance
from cyelec.solver.grouper import generate_circuits, split_oversized_routed_circuits
from cyelec.solver.submain import finalize_submain_cumulative_vd, solve_submain
from cyelec.spatial.placement import build_switch_legs, place_electrical_devices
from cyelec.spatial.router import route_circuits
from cyelec.spatial.wet_zones import compute_building_wet_zones
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
    routed_lengths_m: dict[str, float] | None = None,
    extra_flags: list[ReviewFlag] | None = None,
    precomputed_specs: list[CircuitSpec] | None = None,
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

    # 1. Deterministic circuit grouping (reusing Phase 2 specs when precomputed)
    raw_specs = (
        precomputed_specs
        if precomputed_specs is not None
        else generate_circuits(effective_building, standards, overrides=overrides)
    )
    if routed_lengths_m:
        raw_specs = [
            spec.model_copy(update={"length_m": routed_lengths_m[spec.circuit_id]})
            if spec.circuit_id in routed_lengths_m
            else spec
            for spec in raw_specs
        ]

    # Pre-apply per-circuit overrides (e.g., diversity/fixed_phase/length) before MILP balancing
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

    # 2b. Solve sub-mains (if multi-floor sub-distribution boards are defined)
    prelim_sub_mains: dict[str, SolvedSubMain] = {}
    if effective_building.sub_boards:
        is_comm_ipf = effective_building.building_type in (
            BuildingType.OFFICE,
            BuildingType.SHOP,
            BuildingType.WORKSHOP,
            BuildingType.INDUSTRIAL,
        )
        origin_ipf_ka = (
            overrides.prospective_fault_current_ka
            if (overrides is not None and overrides.prospective_fault_current_ka is not None)
            else (10.0 if is_comm_ipf else 6.0)
        )
        for sb in effective_building.sub_boards:
            b_specs = [s for s in effective_specs if s.board_id == sb.board_id]
            if not b_specs:
                continue
            prelim_sub_mains[sb.board_id] = solve_submain(
                sb,
                b_specs,
                standards,
                earthing_system=earthing_system,
                ze_or_ra_ohm=ze_or_ra_ohm,
                origin_ipf_ka=origin_ipf_ka,
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

        circuit_spec_for_solve = raw_spec
        board_ze_ra = ze_or_ra_ohm
        sub_m = prelim_sub_mains.get(raw_spec.board_id)
        if sub_m is not None:
            # Enforce BS 7671 cumulative ΔV: ΔV_submain + ΔV_final <= 3% (lighting) / 5% (power)
            remaining_vd = max(0.5, round(raw_spec.vd_limit_pct - sub_m.voltage_drop_pct, 2))
            circuit_spec_for_solve = raw_spec.model_copy(update={"vd_limit_pct": remaining_vd})
            if earthing_system != EarthingSystem.TT:
                board_ze_ra = round(ze_or_ra_ohm + sub_m.r1_plus_r2_ohm, 4)

        solved = solve_circuit(
            circuit_spec_for_solve,
            standards,
            earthing_system=earthing_system,
            ze_or_ra_ohm=board_ze_ra,
            phase=assigned_phase,
            override=ov,
        )
        solved_circuits.append(solved)

    # Finalize cumulative ΔV check per sub-board
    solved_sub_mains: list[SolvedSubMain] = []
    for sb in effective_building.sub_boards:
        prelim = prelim_sub_mains.get(sb.board_id)
        if prelim is None:
            continue
        board_solved = [c for c in solved_circuits if c.spec.board_id == sb.board_id]
        solved_sub_mains.append(
            finalize_submain_cumulative_vd(prelim, board_solved, standards)
        )

    # 4. Cyprus EAC supply & escalation checks
    eac_compliance = evaluate_eac_compliance(
        effective_building,
        solved_circuits,
        standards,
        eac_profile=eac_profile,
        phase_balance=phase_balance,
        pv_capacity_kva_override=pv_kva,
        prospective_fault_current_ka=(
            overrides.prospective_fault_current_ka if overrides is not None else None
        ),
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
    if extra_flags:
        review_flags.extend(extra_flags)
    review_flags.extend(eac_compliance.flags)
    if phase_balance is not None:
        review_flags.extend(phase_balance.flags)
    for sm in solved_sub_mains:
        review_flags.extend(sm.flags)
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

    report_language = overrides.report_language if overrides is not None else ReportLanguage.EN
    branding = overrides.branding if overrides is not None else None

    is_commercial = effective_building.building_type in (
        BuildingType.OFFICE,
        BuildingType.SHOP,
        BuildingType.WORKSHOP,
        BuildingType.INDUSTRIAL,
    )
    eff_ipf_ka = (
        overrides.prospective_fault_current_ka
        if (overrides is not None and overrides.prospective_fault_current_ka is not None)
        else (10.0 if (eac_compliance.ct_metering_required or is_commercial) else 6.0)
    )

    result = ElectricalDesignResult(
        provenance=provenance,
        building=effective_building,
        earthing_system=earthing_system,
        ze_or_ra_ohm=ze_or_ra_ohm,
        prospective_fault_current_ka=eff_ipf_ka,
        circuits=solved_circuits,
        sub_mains=solved_sub_mains,
        phase_balance=phase_balance,
        eac_compliance=eac_compliance,
        report_language=report_language,
        branding=branding,
        include_tt_main_rcd_s=(
            overrides.include_tt_main_rcd_s if overrides is not None else True
        ),
        eac_metadata=overrides.eac_metadata if overrides is not None else None,
        review_flags=review_flags,
        has_blockers=has_blockers,
        etek_signoff_required=True,
    )
    boq = generate_boq(
        result,
        standards,
        rate_overrides=overrides.boq_rate_overrides if overrides is not None else None,
        tender_blank_rates=overrides.tender_boq_blank_rates if overrides is not None else False,
    )
    return result.model_copy(update={"boq": boq})


def _apply_building_overrides(
    building: BuildingModel,
    overrides: DesignOverrides | None,
    standards: StandardsBundle,
) -> tuple[BuildingModel, list[ReviewFlag]]:
    """Apply Pass-2 room-type and DB-location overrides before spatial & electrical solving."""
    if overrides is None:
        return building, []

    flags: list[ReviewFlag] = []
    updated = building

    if overrides.room_type_overrides:
        new_rooms = []
        for room in updated.rooms:
            new_type = overrides.room_type_overrides.get(room.id)
            if new_type is not None and new_type != room.normalized_type:
                tmpl = standards.room_templates.rooms[new_type]
                new_rooms.append(
                    room.model_copy(
                        update={
                            "normalized_type": new_type,
                            "is_wet_room": tmpl.is_wet_room,
                            "is_outdoor": tmpl.is_outdoor,
                        }
                    )
                )
                flags.append(
                    ReviewFlag(
                        code="ROOM_TYPE_OVERRIDDEN",
                        severity=ReviewSeverity.INFO,
                        category="geometry",
                        message=(
                            f"Room {room.id} ('{room.raw_label}') reclassified by engineer "
                            f"override: {room.normalized_type.value} -> {new_type.value}."
                        ),
                    )
                )
            else:
                new_rooms.append(room)
        updated = updated.model_copy(update={"rooms": new_rooms})

    if overrides.db_location_override_mm is not None:
        loc = overrides.db_location_override_mm
        updated = updated.model_copy(
            update={
                "db_anchor": updated.db_anchor.model_copy(
                    update={"location_mm": loc, "wall_id": None}
                )
            }
        )
        flags.append(
            ReviewFlag(
                code="DB_LOCATION_OVERRIDDEN",
                severity=ReviewSeverity.INFO,
                category="geometry",
                message=(
                    f"Main distribution board {updated.db_anchor.id} relocated by engineer "
                    f"override to ({loc.x_mm:.0f}, {loc.y_mm:.0f} mm); all conduits re-routed."
                ),
            )
        )

    return updated, flags


def run_phase2_pipeline(
    source: Path | BuildingModel,
    *,
    overrides: DesignOverrides | None = None,
    standards_dir: Path | None = None,
    building_type: BuildingType | None = None,
    project_name: str | None = None,
    tier2_fallback: RoomClassifierFallback | None = None,
    layer_mapping: DxfLayerMapping | None = None,
    extra_flags: list[ReviewFlag] | None = None,
) -> Phase2DesignResult:
    """Execute stateless Phase 2 pipeline: DXF/IFC ingest, wet zones, placement, routing, solve."""
    standards = load_standards(standards_dir)
    eac_profile: EacProfile = (
        overrides.eac_profile
        if (overrides is not None and overrides.eac_profile is not None)
        else standards.eac_rules.default_profile
    )

    spatial_flags: list[ReviewFlag] = list(extra_flags) if extra_flags else []

    # 1. Architectural ingestion (DXF / DWG / IFC) or direct BuildingModel
    if isinstance(source, Path):
        suffix = source.suffix.lower()
        if suffix == ".dxf":
            building, ingest_flags = ingest_dxf(
                source,
                standards=standards,
                project_name=project_name,
                building_type=building_type,
                tier2_fallback=tier2_fallback,
                layer_mapping=layer_mapping,
            )
            spatial_flags.extend(ingest_flags)
        elif suffix == ".dwg":
            with tempfile.TemporaryDirectory(prefix="cyelec_dwg_") as dwg_tmp:
                converted_dxf, dwg_flag = convert_dwg_to_dxf(source, Path(dwg_tmp))
                spatial_flags.append(dwg_flag)
                building, ingest_flags = ingest_dxf(
                    converted_dxf,
                    standards=standards,
                    project_name=project_name or source.stem.replace("_", " ").title(),
                    building_type=building_type,
                    tier2_fallback=tier2_fallback,
                    layer_mapping=layer_mapping,
                )
                building = building.model_copy(update={"source_file": str(source)})
                spatial_flags.extend(ingest_flags)
        elif suffix == ".ifc":
            building, ingest_flags = ingest_ifc(
                source,
                standards=standards,
                project_name=project_name,
                building_type=building_type,
                tier2_fallback=tier2_fallback,
            )
            spatial_flags.extend(ingest_flags)
        else:
            raise ValueError(
                f"Unsupported architectural input format: '{suffix}' (use .dxf/.dwg/.ifc)"
            )
    else:
        building = source

    # 1b. Apply Pass-2 visual room-type & DB-location overrides before spatial layout
    building, ov_flags = _apply_building_overrides(building, overrides, standards)
    spatial_flags.extend(ov_flags)

    # 2. BS 7671 Section 701 wet zones (clipped to room walls)
    wet_zones = compute_building_wet_zones(building, standards, eac_profile=eac_profile)
    wz_room_ids = {w.room_id for w in wet_zones}
    for room in building.rooms:
        if (
            room.normalized_type in (RoomType.BATHROOM, RoomType.ENSUITE)
            and room.id not in wz_room_ids
        ):
            spatial_flags.append(
                ReviewFlag(
                    code="WET_ROOM_NO_FIXTURE_GEOMETRY",
                    severity=ReviewSeverity.WARNING,
                    category="bs7671",
                    regulation_ref="BS 7671 Section 701",
                    message=(
                        f"Wet room {room.id} ('{room.raw_label}') has no bathtub/shower fixture "
                        "polygon in the source CAD; Section 701 Zone 0/1/2 boundaries could not "
                        "be verified automatically — confirm fixture & IP44/socket clearances."
                    ),
                )
            )

    # 3. Deterministic valid-wall electrical accessory placement
    placed_devices, placement_flags = place_electrical_devices(building, wet_zones, standards)
    spatial_flags.extend(placement_flags)

    # 4. Generate circuit list, route on NetworkX conduit graph, and auto-split over-long loops
    specs = generate_circuits(building, standards, overrides=overrides)
    bound_devices, routed_circuits, routing_flags = route_circuits(
        building, specs, placed_devices
    )
    routed_lengths_m = {rc.circuit_id: rc.total_routed_length_m for rc in routed_circuits}

    for _ in range(2):
        specs, split_flags, did_split = split_oversized_routed_circuits(
            building, specs, routed_lengths_m, standards, overrides=overrides
        )
        if not did_split:
            break
        spatial_flags.extend(split_flags)
        bound_devices, routed_circuits, routing_flags = route_circuits(
            building, specs, placed_devices
        )
        routed_lengths_m = {rc.circuit_id: rc.total_routed_length_m for rc in routed_circuits}

    spatial_flags.extend(routing_flags)

    # 4b. BS 7671 Table 4C1 corridor conduit grouping check / optional trunking Cg propagation
    trunk_counts = {rc.circuit_id: rc.max_parallel_circuits_on_trunk for rc in routed_circuits}
    peak_corridor = max(trunk_counts.values(), default=1)
    apply_cg = bool(overrides is not None and overrides.apply_corridor_grouping)
    if apply_cg and peak_corridor > 1:
        updated_specs = []
        affected = 0
        explicit_ov = overrides.circuit_overrides if overrides is not None else {}
        for sp in specs:
            par = trunk_counts.get(sp.circuit_id, 1)
            has_manual_cg = (
                sp.circuit_id in explicit_ov
                and explicit_ov[sp.circuit_id].grouping_count is not None
            )
            if par > 1 and not has_manual_cg:
                # Cap practical shared trunking bundle at 6 (BS 7671 Table 4C1 Cg=0.57)
                eff_grp = min(par, 6)
                updated_specs.append(sp.model_copy(update={"grouping_count": eff_grp}))
                affected += 1
            else:
                updated_specs.append(sp)
        specs = updated_specs
        spatial_flags.append(
            ReviewFlag(
                code="CORRIDOR_CG_GROUPING_APPLIED",
                severity=ReviewSeverity.INFO,
                category="bs7671",
                regulation_ref="BS 7671 Table 4C1 (Grouping Factor Cg)",
                message=(
                    f"Trunking/tray grouping mode enabled: applied BS 7671 Table 4C1 Cg derating "
                    f"to {affected} co-routed circuit(s) (peak corridor density: {peak_corridor})."
                ),
            )
        )
    elif peak_corridor >= 4:
        ref_cg, _ = standards.cables.get_cg(min(peak_corridor, 6))
        spatial_flags.append(
            ReviewFlag(
                code="CONDUIT_CORRIDOR_GROUPING_CHECK",
                severity=ReviewSeverity.WARNING,
                category="bs7671",
                regulation_ref="BS 7671 Table 4C1 (Grouping Factor Cg)",
                message=(
                    f"Peak {peak_corridor} circuits share a main corridor/DB departure route. "
                    "Separate recessed Cyprus masonry PVC conduits (>=2xD spacing) retain Cg=1.00; "
                    f"if installed in shared trunking/cable tray, enable 'Apply Corridor Cg "
                    f"Grouping' (Table 4C1 Cg={ref_cg:.2f} for 6 grouped circuits)."
                ),
                requires_etek_signoff=True,
            )
        )

    # 5. Feed true NetworkX routed lengths and precomputed specs into Phase 1 solvers
    design = run_phase1_pipeline(
        building,
        overrides=overrides,
        standards_dir=standards_dir,
        routed_lengths_m=routed_lengths_m,
        extra_flags=spatial_flags,
        precomputed_specs=specs,
    )

    # Keep spatial total conduit length synced with final effective circuit lengths
    total_conduit_m = round(sum(c.spec.length_m for c in design.circuits), 2)
    spatial = SpatialLayoutResult(
        wet_zones=wet_zones,
        devices=bound_devices,
        switch_legs=build_switch_legs(bound_devices),
        routed_circuits=routed_circuits,
        total_conduit_m=total_conduit_m,
        flags=spatial_flags,
    )

    # Refresh BOQ with exact placed spatial accessory counts & custom/tender rate settings
    refreshed_boq = generate_boq(
        design,
        standards,
        spatial=spatial,
        rate_overrides=overrides.boq_rate_overrides if overrides is not None else None,
        tender_blank_rates=overrides.tender_boq_blank_rates if overrides is not None else False,
    )
    design = design.model_copy(update={"boq": refreshed_boq})

    return Phase2DesignResult(design=design, spatial=spatial)


def export_phase1_deliverables(
    result: ElectricalDesignResult,
    out_dir: Path,
) -> dict[str, Path]:
    """Write JSON snapshot, formula-backed Excel, CSVs, Markdown, and A4-landscape HTML outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "design_result.json"
    xlsx_path = out_dir / "load_schedule.xlsx"
    csv_path = out_dir / "load_schedule.csv"
    boq_csv_path = out_dir / "boq.csv"
    md_path = out_dir / "calc_report.md"
    html_path = out_dir / "calc_report.html"

    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    export_load_schedule_xlsx(result, xlsx_path)
    export_load_schedule_csv(result, csv_path)
    export_boq_csv(result, boq_csv_path)
    export_calc_reports(result, md_path, html_path)

    return {
        "json": json_path,
        "xlsx": xlsx_path,
        "csv": csv_path,
        "boq_csv": boq_csv_path,
        "md": md_path,
        "html": html_path,
    }


def export_phase2_deliverables(
    result: Phase2DesignResult,
    out_dir: Path,
    *,
    source_dxf_path: Path | None = None,
) -> dict[str, Path]:
    """Write all Phase 1 deliverables plus spatial_layout.json and electrical_layout.dxf."""
    outputs = export_phase1_deliverables(result.design, out_dir)
    # Refresh Excel Sheet 6 with exact placed luminaires, switches & bathroom accessories
    export_load_schedule_xlsx(result.design, outputs["xlsx"], spatial=result.spatial)

    spatial_json = out_dir / "spatial_layout.json"
    layout_dxf = out_dir / "electrical_layout.dxf"

    spatial_json.write_text(result.spatial.model_dump_json(indent=2), encoding="utf-8")
    export_electrical_layout_dxf(result, layout_dxf, source_dxf_path=source_dxf_path)

    outputs["spatial_json"] = spatial_json
    outputs["layout_dxf"] = layout_dxf
    return outputs


def export_phase3_deliverables(
    result: Phase2DesignResult,
    out_dir: Path,
    *,
    run_qet_subprocess: bool = False,
    export_pdf: bool = False,
    source_dxf_path: Path | None = None,
) -> dict[str, Path]:
    """Write complete EAC & Construction LV Pack: E-101/E-102/E-201/E-301 DXF/SVG/PDF + forms."""
    from cyelec.render.details_dxf import export_details_notes
    from cyelec.render.eac_forms import export_eac_application_form
    from cyelec.render.eac_pack import (
        render_eac_submission_pack_html,
        write_deliverables_manifest,
    )
    from cyelec.render.floorplan_svg import render_floorplan_svg
    from cyelec.render.layout_dxf import export_electrical_layout_dxf as export_disc_dxf
    from cyelec.render.pdf_export import export_html_to_pdf, export_svg_sheet_to_pdf
    from cyelec.render.qet_bridge import export_qet_project, render_qet_via_subprocess
    from cyelec.render.sld import export_sld_deliverables

    # If the source was a real .dwg file on disk and no pre-converted DXF path was supplied,
    # convert it once inside a scoped temp directory so E-100/E-101/E-102 keep the real underlay.
    dwg_ctx: tempfile.TemporaryDirectory[str] | None = None
    resolved_dxf = source_dxf_path
    if resolved_dxf is None:
        raw_src = result.design.building.source_file
        if raw_src and "," not in raw_src:
            src_p = Path(raw_src)
            if src_p.is_file() and src_p.suffix.lower() == ".dwg":
                try:
                    dwg_ctx = tempfile.TemporaryDirectory(prefix="cyelec_dwg_underlay_")
                    resolved_dxf, _ = convert_dwg_to_dxf(src_p, Path(dwg_ctx.name))
                except Exception:
                    if dwg_ctx is not None:
                        dwg_ctx.cleanup()
                        dwg_ctx = None
                    resolved_dxf = None

    try:
        outputs = export_phase2_deliverables(result, out_dir, source_dxf_path=resolved_dxf)

        eac_form_outputs = export_eac_application_form(result, out_dir, export_pdf=export_pdf)
        outputs.update(eac_form_outputs)

        # Discipline-separated DXFs: E-101 Lighting & Switching, E-102 Power & Accessories
        e101_dxf = export_disc_dxf(
            result,
            out_dir / "E101_lighting_layout.dxf",
            source_dxf_path=resolved_dxf,
            discipline="lighting",
        )
        e102_dxf = export_disc_dxf(
            result,
            out_dir / "E102_power_layout.dxf",
            source_dxf_path=resolved_dxf,
            discipline="power",
        )
        outputs["e101_lighting_dxf"] = e101_dxf
        outputs["e102_power_dxf"] = e102_dxf
    finally:
        if dwg_ctx is not None:
            dwg_ctx.cleanup()

    # Vector SVG Sheets: E-101 Lighting, E-102 Power, and Combined Coordination Overlay
    e101_svg_path = out_dir / "E101_lighting_layout.svg"
    e101_svg_path.write_text(render_floorplan_svg(result, discipline="lighting"), encoding="utf-8")
    outputs["e101_lighting_svg"] = e101_svg_path

    e102_svg_path = out_dir / "E102_power_layout.svg"
    e102_svg_path.write_text(render_floorplan_svg(result, discipline="power"), encoding="utf-8")
    outputs["e102_power_svg"] = e102_svg_path

    floorplan_svg_path = out_dir / "floorplan_overlay.svg"
    floorplan_svg_text = render_floorplan_svg(result, discipline="combined")
    floorplan_svg_path.write_text(floorplan_svg_text, encoding="utf-8")
    outputs["floorplan_svg"] = floorplan_svg_path

    # E-201 Single-Line Diagram (plus backward-compatible single_line_diagram.* copies)
    sld_svg_path = out_dir / "single_line_diagram.svg"
    sld_dxf_path = out_dir / "single_line_diagram.dxf"
    e201_svg_path = out_dir / "E201_single_line_diagram.svg"
    e201_dxf_path = out_dir / "E201_single_line_diagram.dxf"
    qet_path = out_dir / "single_line_diagram.qet"

    export_sld_deliverables(result.design, sld_svg_path, sld_dxf_path)
    e201_svg_path.write_bytes(sld_svg_path.read_bytes())
    e201_dxf_path.write_bytes(sld_dxf_path.read_bytes())
    export_qet_project(result.design, qet_path)

    outputs["sld_svg"] = sld_svg_path
    outputs["sld_dxf"] = sld_dxf_path
    outputs["e201_sld_svg"] = e201_svg_path
    outputs["e201_sld_dxf"] = e201_dxf_path
    outputs["sld_qet"] = qet_path

    # E-301 Construction Details, Earthing/Meter/Wet-Zone Sections & General Notes
    details_out = export_details_notes(result, out_dir)
    outputs.update(details_out)

    if run_qet_subprocess:
        qet_status = render_qet_via_subprocess(qet_path, out_dir / "qet_render", fmt="svg")
        if qet_status.rendered_output_path is not None:
            outputs["qet_rendered_svg"] = qet_status.rendered_output_path

    pack_html_path = out_dir / "eac_submission_pack.html"
    pack_html_path.write_text(
        render_eac_submission_pack_html(
            result,
            sld_svg=sld_svg_path.read_text(encoding="utf-8"),
            floorplan_svg=floorplan_svg_text,
            deliverables=outputs,
        ),
        encoding="utf-8",
    )
    outputs["eac_pack_html"] = pack_html_path

    if export_pdf:
        calc_pdf, _ = export_html_to_pdf(outputs["html"], out_dir / "calc_report.pdf")
        if calc_pdf is not None:
            outputs["calc_report_pdf"] = calc_pdf
        pack_pdf, _ = export_html_to_pdf(pack_html_path, out_dir / "eac_submission_pack.pdf")
        if pack_pdf is not None:
            outputs["eac_pack_pdf"] = pack_pdf
        # Printable A3 landscape PDF drawing sheets for site electrician & EAC filing
        for key, src_svg, out_name in (
            ("e101_lighting_pdf", e101_svg_path, "E101_lighting_layout.pdf"),
            ("e102_power_pdf", e102_svg_path, "E102_power_layout.pdf"),
            ("e201_sld_pdf", e201_svg_path, "E201_single_line_diagram.pdf"),
            ("e301_details_pdf", details_out["e301_details_svg"], "E301_details_notes.pdf"),
        ):
            pdf_p, _ = export_svg_sheet_to_pdf(src_svg, out_dir / out_name)
            if pdf_p is not None:
                outputs[key] = pdf_p

    manifest_path = out_dir / "manifest.json"
    write_deliverables_manifest(result, outputs, manifest_path)
    outputs["manifest_json"] = manifest_path
    return outputs
