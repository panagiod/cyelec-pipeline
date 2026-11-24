"""Typer CLI entry point for cyelec-pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cyelec import __version__
from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.models.building import BuildingType
from cyelec.models.electrical import DesignOverrides, EacProfile, EarthingSystem, ReviewSeverity
from cyelec.pipeline import (
    export_phase1_deliverables,
    export_phase2_deliverables,
    run_phase1_pipeline,
    run_phase2_pipeline,
)
from cyelec.standards_loader import load_standards, resolve_standards_dir
from cyelec.synth.sample_plan import generate_cyprus_3bed_dxf

app = typer.Typer(
    name="cyelec",
    help="Deterministic BS 7671 / IEC 60364 / Cyprus EAC electrical engineering pipeline.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print cyelec-pipeline version."""
    typer.echo(f"cyelec-pipeline {__version__}")


@app.command("check-standards")
def check_standards(
    standards_dir: Annotated[
        Path | None,
        typer.Option("--standards-dir", help="Optional path to standards/ directory."),
    ] = None,
) -> None:
    """Load and strictly validate all BS 7671 and Cyprus EAC YAML standards tables."""
    resolved = resolve_standards_dir(standards_dir)
    bundle = load_standards(resolved)
    typer.echo(f"Standards directory: {resolved}")
    for name, ver in sorted(bundle.versions_map().items()):
        typer.echo(f"  [OK] {name}: {ver}")
    typer.echo(
        f"Loaded {len(bundle.cables.cables)} cable sizes, "
        f"{len(bundle.devices.standard_ratings_a)} device ratings, "
        f"{len(bundle.eac_rules.profiles)} EAC profiles, "
        f"{len(bundle.room_templates.rooms)} room templates."
    )


@app.command("synth-plan")
def synth_plan(
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output R2018 DXF path."),
    ] = Path("out/cyprus_3bed/sample_plan.dxf"),
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Optional output path for ground-truth BuildingModel JSON."),
    ] = None,
    pv_kva: Annotated[
        float,
        typer.Option("--pv-kva", help="Optional rooftop PV capacity in kVA."),
    ] = 0.0,
) -> None:
    """Generate the synthetic 120 m² Cyprus 3-bedroom apartment R2018 DXF fixture."""
    model = generate_cyprus_3bed_dxf(out_dxf=out, pv_capacity_kva=pv_kva)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    typer.echo(f"Generated R2018 DXF: {out}")
    typer.echo(
        f"Rooms: {len(model.rooms)} | Total Area: {model.total_floor_area_m2:.2f} m² | "
        f"SHA-256: {(model.source_sha256 or '')[:16]}"
    )


@app.command("solve-synth")
def solve_synth(
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o", help="Output directory for DXF and Phase 1 deliverables."),
    ] = Path("out/cyprus_3bed"),
    profile: Annotated[
        EacProfile,
        typer.Option("--profile", help="Cyprus EAC BS 7671 edition profile."),
    ] = EacProfile.EAC_18TH_A2,
    earthing: Annotated[
        EarthingSystem,
        typer.Option("--earthing", help="Site earthing system (Cyprus default: TT)."),
    ] = EarthingSystem.TT,
    ambient_c: Annotated[
        int | None,
        typer.Option("--ambient", help="Optional global ambient temperature override (°C)."),
    ] = None,
    pv_kva: Annotated[
        float,
        typer.Option("--pv-kva", help="Optional rooftop PV capacity in kVA."),
    ] = 0.0,
) -> None:
    """Generate the synthetic 3-bed Cyprus apartment DXF and run the full Phase 1 pipeline."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_path = out_dir / "cyprus_3bed_120m2.dxf"
    building = generate_cyprus_3bed_dxf(out_dxf=dxf_path, pv_capacity_kva=pv_kva)

    overrides = DesignOverrides(
        eac_profile=profile,
        earthing_system=earthing,
        ambient_temp_c=ambient_c,
        pv_capacity_kva=pv_kva,
    )
    result = run_phase1_pipeline(building, overrides=overrides)
    outputs = export_phase1_deliverables(result, out_dir)

    blockers = sum(1 for f in result.review_flags if f.severity == ReviewSeverity.BLOCKER)
    warnings = sum(1 for f in result.review_flags if f.severity == ReviewSeverity.WARNING)
    infos = sum(1 for f in result.review_flags if f.severity == ReviewSeverity.INFO)

    typer.echo(f"Project: {result.building.project_name}")
    typer.echo(
        f"Profile: {result.eac_compliance.eac_profile.value} | "
        f"Earthing: {result.earthing_system.value} (Ze/Ra={result.ze_or_ra_ohm:.1f} Ω)"
    )
    typer.echo(
        f"Circuits: {len(result.circuits)} | "
        f"Connected: {result.eac_compliance.total_connected_kva:.2f} kVA | "
        f"Diversified: {result.eac_compliance.total_diversified_kva:.2f} kVA | "
        f"Supply: {result.eac_compliance.recommended_supply_tier}"
    )
    if result.phase_balance is not None:
        pb = result.phase_balance
        typer.echo(
            f"3-Phase Balance (MILP {pb.solver_status}): "
            f"L1={pb.phase_currents_a['L1']:.2f} A, "
            f"L2={pb.phase_currents_a['L2']:.2f} A, "
            f"L3={pb.phase_currents_a['L3']:.2f} A | "
            f"Imbalance={pb.imbalance_pct:.2f}% "
            f"({'PASS' if pb.within_target else 'WARNING'})"
        )
    typer.echo(f"Review Flags: {blockers} blockers, {warnings} warnings, {infos} info")
    typer.echo(f"DXF:  {dxf_path}")
    for key, path in outputs.items():
        typer.echo(f"{key.upper():<4}: {path}")


@app.command("inspect-dxf")
def inspect_dxf_cmd(
    dxf_path: Annotated[
        Path,
        typer.Argument(help="Path to architectural .dxf floorplan."),
    ],
) -> None:
    """Ingest a DXF floorplan and print bilingual classified rooms, fixtures, doors, and DB1."""
    model, flags = ingest_dxf(dxf_path)
    typer.echo(f"File: {dxf_path} | SHA-256: {(model.source_sha256 or '')[:16]}")
    typer.echo(
        f"Inferred Building Type: {model.building_type.value} | "
        f"Rooms: {len(model.rooms)} | Total Area: {model.total_floor_area_m2:.2f} m²"
    )
    typer.echo(
        f"Walls: {len(model.walls)} | Doors: {len(model.doors)} | "
        f"Windows: {len(model.windows)} | DB1: "
        f"({model.db_anchor.location_mm.x_mm:.0f}, {model.db_anchor.location_mm.y_mm:.0f} mm)"
    )
    for r in model.rooms:
        fixts = ", ".join(f.fixture_type for f in r.fixtures) or "-"
        typer.echo(
            f"  {r.id:<4} {r.normalized_type.value:<16} {r.area_m2:>6.2f} m² | "
            f"Label: {r.raw_label} | Fixtures: {fixts}"
        )
    if flags:
        typer.echo(f"Ingestion Flags ({len(flags)}):")
        for f in flags:
            typer.echo(f"  [{f.severity.value.upper()}] {f.code}: {f.message}")


@app.command("solve-dxf")
def solve_dxf(
    input_path: Annotated[
        Path,
        typer.Argument(help="Input architectural .dxf (or .ifc) floorplan."),
    ],
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o", help="Output directory for Phase 2 deliverables."),
    ] = Path("out/phase2"),
    profile: Annotated[
        EacProfile,
        typer.Option("--profile", help="Cyprus EAC BS 7671 edition profile."),
    ] = EacProfile.EAC_18TH_A2,
    earthing: Annotated[
        EarthingSystem,
        typer.Option("--earthing", help="Site earthing system (Cyprus default: TT)."),
    ] = EarthingSystem.TT,
    building_type: Annotated[
        BuildingType | None,
        typer.Option("--building-type", help="Optional EAC building type override."),
    ] = None,
    ambient_c: Annotated[
        int | None,
        typer.Option("--ambient", help="Optional global ambient temperature override (°C)."),
    ] = None,
    pv_kva: Annotated[
        float,
        typer.Option("--pv-kva", help="Optional rooftop PV capacity in kVA."),
    ] = 0.0,
) -> None:
    """Run end-to-end Phase 2 pipeline from a DXF/IFC plan to layout DXF, Excel, and reports."""
    overrides = DesignOverrides(
        eac_profile=profile,
        earthing_system=earthing,
        ambient_temp_c=ambient_c,
        pv_capacity_kva=pv_kva,
    )
    p2 = run_phase2_pipeline(input_path, overrides=overrides, building_type=building_type)
    outputs = export_phase2_deliverables(p2, out_dir)
    res = p2.design
    sp = p2.spatial

    blockers = sum(1 for f in res.review_flags if f.severity == ReviewSeverity.BLOCKER)
    warnings = sum(1 for f in res.review_flags if f.severity == ReviewSeverity.WARNING)
    infos = sum(1 for f in res.review_flags if f.severity == ReviewSeverity.INFO)

    typer.echo(f"Project: {res.building.project_name} ({res.building.total_floor_area_m2:.2f} m²)")
    typer.echo(
        f"Spatial: {len(sp.wet_zones)} wet-zone room(s) | {len(sp.devices)} placed devices | "
        f"Total NetworkX Routed Conduit: {sp.total_conduit_m:.1f} m"
    )
    typer.echo(
        f"Circuits: {len(res.circuits)} | "
        f"Connected: {res.eac_compliance.total_connected_kva:.2f} kVA | "
        f"Diversified: {res.eac_compliance.total_diversified_kva:.2f} kVA | "
        f"Supply: {res.eac_compliance.recommended_supply_tier}"
    )
    if res.phase_balance is not None:
        pb = res.phase_balance
        typer.echo(
            f"3-Phase Balance (MILP {pb.solver_status}): "
            f"L1={pb.phase_currents_a['L1']:.2f} A, "
            f"L2={pb.phase_currents_a['L2']:.2f} A, "
            f"L3={pb.phase_currents_a['L3']:.2f} A | "
            f"Imbalance={pb.imbalance_pct:.2f}% "
            f"({'PASS' if pb.within_target else 'WARNING'})"
        )
    typer.echo(f"Review Flags: {blockers} blockers, {warnings} warnings, {infos} info")
    for key, path in outputs.items():
        typer.echo(f"{key.upper():<12}: {path}")


if __name__ == "__main__":
    app()
