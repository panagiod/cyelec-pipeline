"""Typer CLI entry point for cyelec-pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cyelec import __version__
from cyelec.ingest.dxf_ingest import ingest_dxf, inspect_dxf_layers
from cyelec.models.building import BuildingType, DxfLayerMapping
from cyelec.models.electrical import (
    DesignOverrides,
    EacProfile,
    EarthingSystem,
    ElectricalDesignResult,
    ReportLanguage,
    ReviewSeverity,
    TenantBranding,
)
from cyelec.pipeline import (
    export_phase1_deliverables,
    export_phase2_deliverables,
    export_phase3_deliverables,
    run_phase1_pipeline,
    run_phase2_pipeline,
)
from cyelec.render.eac_pack import build_submission_zip
from cyelec.render.qet_bridge import export_qet_project
from cyelec.render.sld import export_sld_deliverables
from cyelec.standards_loader import load_standards, resolve_standards_dir
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf
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
    template: Annotated[
        BenchmarkTemplate,
        typer.Option("--template", help="Cyprus architectural benchmark template."),
    ] = BenchmarkTemplate.APARTMENT_3BED,
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Optional output path for ground-truth BuildingModel JSON."),
    ] = None,
    pv_kva: Annotated[
        float | None,
        typer.Option("--pv-kva", help="Optional rooftop PV capacity override in kVA."),
    ] = None,
) -> None:
    """Generate a synthetic Cyprus architectural benchmark R2018 DXF fixture."""
    model = generate_benchmark_dxf(template, out_dxf=out)
    if pv_kva is not None:
        model = model.model_copy(update={"pv_capacity_kva": pv_kva})
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    typer.echo(f"Generated R2018 DXF ({template.value}): {out}")
    typer.echo(
        f"Rooms: {len(model.rooms)} | Total Area: {model.total_floor_area_m2:.2f} m² | "
        f"PV: {model.pv_capacity_kva:.1f} kVA | EV: {model.ev_charger_kw:.1f} kW | "
        f"SHA-256: {(model.source_sha256 or '')[:16]}"
    )


@app.command("benchmark-suite")
def benchmark_suite(
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o", help="Output root directory for all Cyprus benchmarks."),
    ] = Path("out/benchmarks"),
) -> None:
    """Run all 3 Cyprus benchmarks (Apartment, Villa PV+EV, Commercial Office) end-to-end."""
    for tmpl in BenchmarkTemplate:
        sub = out_dir / tmpl.value
        sub.mkdir(parents=True, exist_ok=True)
        dxf_path = sub / f"{tmpl.value}.dxf"
        building = generate_benchmark_dxf(tmpl, out_dxf=dxf_path)
        p2 = run_phase2_pipeline(building)
        outputs = export_phase3_deliverables(p2, sub)
        eac = p2.design.eac_compliance
        pb = p2.design.phase_balance
        imb = f"{pb.imbalance_pct:.2f}%" if pb is not None else "1P"
        typer.echo(
            f"[{tmpl.value:<17}] {building.total_floor_area_m2:>6.1f} m² | "
            f"{len(p2.design.circuits):>2} ckts | {eac.total_diversified_kva:>6.1f} kVA | "
            f"{eac.recommended_supply_tier:<34} | Imb {imb:<6} | "
            f"CT={'YES' if eac.ct_metering_required else 'no':<3} | "
            f"SubWarn={'YES' if eac.substation_warning else 'no':<3} | "
            f"{len(outputs)} files"
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


def _load_layer_mapping(layer_mapping_path: Path | None) -> DxfLayerMapping | None:
    if layer_mapping_path is None:
        return None
    return DxfLayerMapping.model_validate_json(layer_mapping_path.read_text(encoding="utf-8"))


@app.command("inspect-dxf")
def inspect_dxf_cmd(
    dxf_path: Annotated[
        Path,
        typer.Argument(help="Path to architectural .dxf floorplan."),
    ],
    layers: Annotated[
        bool,
        typer.Option("--layers", help="List discovered DXF layers, counts, and inferred roles."),
    ] = False,
    layer_mapping: Annotated[
        Path | None,
        typer.Option("--layer-mapping", help="Optional JSON file with custom DxfLayerMapping."),
    ] = None,
) -> None:
    """Ingest a DXF floorplan and print bilingual classified rooms, fixtures, doors, and DB1."""
    if layers:
        discovered = inspect_dxf_layers(dxf_path)
        typer.echo(f"Discovered {len(discovered)} DXF layer(s) in {dxf_path}:")
        for info in discovered:
            types_str = ", ".join(f"{k}:{v}" for k, v in sorted(info.entity_types.items()))
            typer.echo(
                f"  {info.layer_name:<24} role={info.inferred_role:<9} "
                f"count={info.entity_count:<4} [{types_str}]"
            )
        return

    mapping = _load_layer_mapping(layer_mapping)
    model, flags = ingest_dxf(dxf_path, layer_mapping=mapping)
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
    layer_mapping: Annotated[
        Path | None,
        typer.Option("--layer-mapping", help="Optional JSON file with custom DxfLayerMapping."),
    ] = None,
) -> None:
    """Run end-to-end Phase 2 pipeline from a DXF/IFC plan to layout DXF, Excel, and reports."""
    overrides = DesignOverrides(
        eac_profile=profile,
        earthing_system=earthing,
        ambient_temp_c=ambient_c,
        pv_capacity_kva=pv_kva,
    )
    mapping = _load_layer_mapping(layer_mapping)
    p2 = run_phase2_pipeline(
        input_path,
        overrides=overrides,
        building_type=building_type,
        layer_mapping=mapping,
    )
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


@app.command("solve-eac")
def solve_eac(
    input_path: Annotated[
        Path,
        typer.Argument(help="Input architectural .dxf (or .ifc) floorplan."),
    ],
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o", help="Output directory for complete EAC submission pack."),
    ] = Path("out/eac_pack"),
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
    layer_mapping: Annotated[
        Path | None,
        typer.Option("--layer-mapping", help="Optional JSON file with custom DxfLayerMapping."),
    ] = None,
    language: Annotated[
        ReportLanguage,
        typer.Option("--language", help="Deliverable language: en, el, or bilingual."),
    ] = ReportLanguage.EN,
    firm_name: Annotated[
        str,
        typer.Option("--firm-name", help="Consulting engineering firm name for branding."),
    ] = "",
    engineer: Annotated[
        str,
        typer.Option("--engineer", help="Licensed ETEK electrical engineer name."),
    ] = "",
    etek_reg: Annotated[
        str,
        typer.Option("--etek-reg", help="ETEK registration number (e.g., ETEK-E-1234)."),
    ] = "",
    export_pdf: Annotated[
        bool,
        typer.Option("--pdf/--no-pdf", help="Export native A4 PDFs via optional WeasyPrint."),
    ] = False,
    zip_bundle: Annotated[
        bool,
        typer.Option("--zip/--no-zip", help="Also write eac_submission_pack.zip archive."),
    ] = True,
    qet_subprocess: Annotated[
        bool,
        typer.Option(
            "--qet-subprocess/--no-qet-subprocess",
            help="Invoke optional isolated GPLv2+ QElectroTech CLI subprocess if installed.",
        ),
    ] = False,
) -> None:
    """Run full Phase 0-3 pipeline and generate complete Cyprus EAC plan-approval pack."""
    branding = (
        TenantBranding(firm_name=firm_name, engineer_name=engineer, etek_reg_no=etek_reg)
        if (firm_name or engineer or etek_reg)
        else None
    )
    overrides = DesignOverrides(
        eac_profile=profile,
        earthing_system=earthing,
        ambient_temp_c=ambient_c,
        pv_capacity_kva=pv_kva,
        report_language=language,
        branding=branding,
    )
    mapping = _load_layer_mapping(layer_mapping)
    p2 = run_phase2_pipeline(
        input_path,
        overrides=overrides,
        building_type=building_type,
        layer_mapping=mapping,
    )
    outputs = export_phase3_deliverables(
        p2,
        out_dir,
        run_qet_subprocess=qet_subprocess,
        export_pdf=export_pdf,
    )
    if zip_bundle:
        outputs["zip_bundle"] = build_submission_zip(
            outputs, out_dir / "eac_submission_pack.zip"
        )

    res = p2.design
    sp = p2.spatial
    boq_msg = f" | BOQ: €{res.boq.grand_total_eur:,.2f}" if res.boq is not None else ""
    typer.echo(
        f"EAC Pack Complete: {res.building.project_name} | "
        f"Supply: {res.eac_compliance.recommended_supply_tier} | "
        f"Conduit: {sp.total_conduit_m:.1f} m{boq_msg} | Artifacts: {len(outputs)}"
    )
    for key, path in sorted(outputs.items()):
        typer.echo(f"  {key.upper():<16}: {path}")


@app.command("export-sld")
def export_sld(
    design_json: Annotated[
        Path,
        typer.Argument(help="Path to a saved design_result.json snapshot."),
    ],
    out_dir: Annotated[
        Path,
        typer.Option("--out-dir", "-o", help="Output directory for SLD SVG, DXF, and QET files."),
    ] = Path("out/sld"),
) -> None:
    """Export Single-Line Diagram (.svg, .dxf, .qet) from any saved design_result.json."""
    design = ElectricalDesignResult.model_validate_json(design_json.read_text(encoding="utf-8"))
    svg_path, dxf_path = export_sld_deliverables(
        design,
        out_dir / "single_line_diagram.svg",
        out_dir / "single_line_diagram.dxf",
    )
    qet_path = export_qet_project(design, out_dir / "single_line_diagram.qet")
    typer.echo(f"SLD SVG: {svg_path}")
    typer.echo(f"SLD DXF: {dxf_path}")
    typer.echo(f"SLD QET: {qet_path}")


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind host address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
) -> None:
    """Start the stateless Multi-Tenant EU B2B SaaS FastAPI service."""
    try:
        import uvicorn
    except ImportError as exc:
        raise typer.BadParameter(
            "API server requires optional 'api' extra: pip install 'cyelec-pipeline[api]'"
        ) from exc

    uvicorn.run("cyelec.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
