"""Integration tests for real-world AutoCAD DXF ingestion fixes & quantitative plan evaluation."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Point, Polygon
from typer.testing import CliRunner

from cyelec.cli import app
from cyelec.eval import (
    evaluate_against_reference,
    export_evaluation_reports,
    load_reference_plan,
)
from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.ingest.room_classifier import classify_room_label
from cyelec.models.building import BuildingType, RoomType
from cyelec.pipeline import run_phase2_pipeline
from cyelec.standards_loader import load_standards
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf

REF_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "eval_references"
runner = CliRunner()


def test_autocad_control_code_stripping() -> None:
    """Legacy AutoCAD %%u / %%o formatting codes must not break room classification."""
    bundle = load_standards()
    cls_k = classify_room_label("%%uKITCHEN", bundle.room_templates, room_id="R1")
    cls_mb = classify_room_label("%%oMSTR. BATH", bundle.room_templates, room_id="R2")
    cls_u = classify_room_label("%%uUTIL.", bundle.room_templates, room_id="R3")

    assert cls_k.normalized_type == RoomType.KITCHEN
    assert cls_k.confidence >= 0.90
    assert cls_mb.normalized_type == RoomType.ENSUITE
    assert cls_u.normalized_type == RoomType.UTILITY


def test_real_autocad_inch_scale_double_walls_open_plan_and_dry_db(tmp_path: Path) -> None:
    """Synthetic reproduction of real-world AutoCAD DXF quirks uncovered on Bishop-Overland.

    Verifies all 4 spatial ingestion fixes at once:
      1. Liar $INSUNITS=4 header on an inch-drawn floorplan auto-corrected via door arc radii.
      2. Double-line architectural walls (no A-ROOM polylines) extracted via wall-cavity fallback.
      3. Multiple texts inside one open-plan room (KITCHEN + GREAT ROOM + door tag 3068)
         merged cleanly to kitchen_living.
      4. DB1 anchor inferred strictly inside a dry indoor room, NEVER inside the bathroom
         even when the bathroom is the first room in the drawing.
    """
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4  # Liar header: claims millimetres, actually drawn in inches
    msp = doc.modelspace()

    # Build a 400 in x 240 in (~10.16 m x 6.10 m) house with 6 in double-line walls:
    # Left top: MSTR. BATH (0..120, 120..240)
    # Left bottom: UTIL. (0..120, 0..120)
    # Right: Open-plan KITCHEN / GREAT ROOM (120..400, 0..240)
    def _draw_double_wall(x0: float, y0: float, x1: float, y1: float, thick: float = 6.0) -> None:
        if abs(y0 - y1) < 1e-6:  # horizontal
            msp.add_line((x0, y0 - thick / 2), (x1, y1 - thick / 2), dxfattribs={"layer": "A-WALL"})
            msp.add_line((x0, y0 + thick / 2), (x1, y1 + thick / 2), dxfattribs={"layer": "A-WALL"})
        else:  # vertical
            msp.add_line((x0 - thick / 2, y0), (x1 - thick / 2, y1), dxfattribs={"layer": "A-WALL"})
            msp.add_line((x0 + thick / 2, y0), (x1 + thick / 2, y1), dxfattribs={"layer": "A-WALL"})

    # Outer perimeter double walls
    _draw_double_wall(0, 0, 400, 0)
    _draw_double_wall(0, 240, 400, 240)
    _draw_double_wall(0, 0, 0, 240)
    _draw_double_wall(400, 0, 400, 240)
    # Interior partition walls
    _draw_double_wall(120, 0, 120, 240)
    _draw_double_wall(0, 120, 120, 120)

    # Door swing arcs in inches (30 in = 762 mm) on A-OPENING layer
    msp.add_arc(
        center=(120, 60),
        radius=30.0,
        start_angle=0,
        end_angle=90,
        dxfattribs={"layer": "A-OPENING"},
    )
    msp.add_arc(
        center=(60, 120),
        radius=30.0,
        start_angle=0,
        end_angle=90,
        dxfattribs={"layer": "A-OPENING"},
    )

    # Room labels with AutoCAD %%u codes + noisy door tag
    msp.add_text("%%uMSTR. BATH", dxfattribs={"layer": "A-NOTE", "insert": (60, 180)})
    msp.add_text("%%uUTIL.", dxfattribs={"layer": "A-NOTE", "insert": (60, 60)})
    msp.add_text("%%uKITCHEN", dxfattribs={"layer": "A-NOTE", "insert": (220, 180)})
    msp.add_text("GREAT ROOM", dxfattribs={"layer": "A-NOTE", "insert": (260, 80)})
    msp.add_text("3068", dxfattribs={"layer": "A-NOTE", "insert": (140, 60)})

    dxf_path = tmp_path / "real_world_inch_house.dxf"
    doc.saveas(dxf_path)

    model, flags = ingest_dxf(dxf_path, building_type=BuildingType.INDEPENDENT_HOUSE)
    flag_codes = {f.code for f in flags}

    # 1. Liar $INSUNITS=4 auto-corrected to inches -> ~60 m² total area
    assert "DXF_SCALE_AUTO_CORRECTED" in flag_codes
    assert 45.0 <= model.total_floor_area_m2 <= 75.0

    # 2. Rooms polygonized and open-plan resolved
    room_types = {r.normalized_type for r in model.rooms}
    assert RoomType.KITCHEN_LIVING in room_types
    assert RoomType.UTILITY in room_types
    assert RoomType.ENSUITE in room_types
    assert "ROOMS_POLYGONIZED_FROM_WALLS" in flag_codes

    # 3. Critical BS 7671 Section 701 safety check: DB1 MUST NOT be inside the wet room
    db_pt = Point(model.db_anchor.location_mm.x_mm, model.db_anchor.location_mm.y_mm)
    for rm in model.rooms:
        if rm.is_wet_room:
            rpoly = Polygon([(p.x_mm, p.y_mm) for p in rm.polygon_mm])
            assert not rpoly.contains(db_pt), f"DB1 placed illegally inside wet room {rm.raw_label}"


@pytest.mark.parametrize(
    ("template", "ref_file"),
    [
        (BenchmarkTemplate.APARTMENT_3BED, "cyprus_3bed_120m2_ref.yaml"),
        (BenchmarkTemplate.VILLA_2STOREY, "cyprus_villa_2storey_ref.yaml"),
        (BenchmarkTemplate.VILLA_PV_EV, "cyprus_villa_pv_ev_ref.yaml"),
        (BenchmarkTemplate.COMMERCIAL_OFFICE, "cyprus_commercial_office_ref.yaml"),
    ],
)
def test_cyprus_reference_plan_evaluations(
    template: BenchmarkTemplate,
    ref_file: str,
    tmp_path: Path,
) -> None:
    """Verify concordance >= 95% and zero safety failures across Cyprus building types."""
    ref = load_reference_plan(REF_DIR / ref_file)
    building = generate_benchmark_dxf(template)
    p2 = run_phase2_pipeline(building)

    report = evaluate_against_reference(p2, ref)
    outputs = export_evaluation_reports(report, tmp_path / template.value)

    assert report.verdict in ("PASS", "PASS_WITH_REVIEW")
    assert report.safety_failures == []
    assert report.all_circuits_bs7671_pass is True
    assert report.db_in_wet_room is False
    assert report.supply_phases_match is True
    assert report.main_breaker_adequate is True
    assert report.concordance_score_pct >= 95.0

    assert outputs["json"].exists()
    assert outputs["md"].exists()
    assert outputs["html"].exists()
    assert "CyElec vs. Real Engineer Plan" in outputs["html"].read_text(encoding="utf-8")


def test_evaluator_detects_undersized_safety_failure() -> None:
    """Evaluator must emit FAIL_SAFETY if any cable is undersized vs licensed engineer reference."""
    ref = load_reference_plan(REF_DIR / "cyprus_3bed_120m2_ref.yaml")
    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(building)

    # Artificially undersize the cooker cable to 2.5 mm² (reference requires 6.0 mm²)
    mutated_circuits = [
        c.model_copy(update={"cable_mm2": 2.5}) if c.spec.category.value == "cooker" else c
        for c in p2.design.circuits
    ]
    mutated_p2 = p2.model_copy(
        update={"design": p2.design.model_copy(update={"circuits": mutated_circuits})}
    )

    report = evaluate_against_reference(mutated_p2, ref)
    assert report.verdict == "FAIL_SAFETY"
    assert any("cooker" in msg for msg in report.safety_failures)
    cooker_row = next(r for r in report.category_comparisons if r.category.value == "cooker")
    assert cooker_row.status == "UNDERSIZED_SAFETY_FAIL"


def test_cli_eval_compare_end_to_end(tmp_path: Path) -> None:
    """End-to-end CLI test for `cyelec eval-compare` on a generated Cyprus DXF."""
    dxf_path = tmp_path / "cyprus_3bed.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=dxf_path)
    out_dir = tmp_path / "eval_out"

    res = runner.invoke(
        app,
        [
            "eval-compare",
            str(dxf_path),
            str(REF_DIR / "cyprus_3bed_120m2_ref.yaml"),
            "-o",
            str(out_dir),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert "Concordance:" in res.stdout
    assert "SAFETY_FAILS=0" in res.stdout
    assert (out_dir / "evaluation_report.html").exists()
    assert (out_dir / "evaluation_report.md").exists()
    assert (out_dir / "evaluation_report.json").exists()


def test_dimension_stripping_and_hall_precedence() -> None:
    """Trailing area/dimension suffixes and 'BEDROOM HALL' must classify accurately."""
    bundle = load_standards()
    assert (
        classify_room_label("MAIN OFFICE 44.9 mp", bundle.room_templates).normalized_type
        == RoomType.LIVING_ROOM
    )
    assert (
        classify_room_label("BEDROOM HALL", bundle.room_templates).normalized_type
        == RoomType.CORRIDOR
    )
    assert (
        classify_room_label("KITCHEN + LIVING", bundle.room_templates).normalized_type
        == RoomType.KITCHEN_LIVING
    )
    assert (
        classify_room_label("PRIMARY BEDROOM 13'-11\" x 12'-1\"", bundle.room_templates)
        .normalized_type
        == RoomType.MASTER_BEDROOM
    )


def test_hybrid_partial_aroom_and_insert_room_tag(tmp_path: Path) -> None:
    """Partial A-ROOM drawings must merge wall-enclosed rooms and read RM-TAG INSERT attributes."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Room 1 (left, 0..4000 x 0..4000) has an explicit A-ROOM outline;
    # Room 2 (right, 4000..8000 x 0..4000) only exists via A-WALL lines + RM-TAG INSERT block.
    msp.add_lwpolyline(
        [(0, 0), (4000, 0), (4000, 4000), (0, 4000)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp.add_text("MAJLIS", dxfattribs={"layer": "A-ROOM-NAME", "insert": (2000, 2000)})

    # Full perimeter + interior partition walls enclosing both rooms
    for a, b in [
        ((0, 0), (8000, 0)),
        ((8000, 0), (8000, 4000)),
        ((8000, 4000), (0, 4000)),
        ((0, 4000), (0, 0)),
        ((4000, 0), (4000, 4000)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})

    blk = doc.blocks.new("RM-TAG")
    blk.add_attdef("NAME", insert=(0, 0))
    ins = msp.add_blockref("RM-TAG", insert=(6000, 2000), dxfattribs={"layer": "A-ROOM-NAME"})
    ins.add_attrib("NAME", "Guest bedroom", insert=(6000, 2000))

    dxf_path = tmp_path / "hybrid_villa.dxf"
    doc.saveas(dxf_path)

    model, flags = ingest_dxf(dxf_path, building_type=BuildingType.INDEPENDENT_HOUSE)
    codes = {f.code for f in flags}
    assert "ROOMS_HYBRID_AUGMENTED_FROM_WALLS" in codes
    assert len(model.rooms) == 2
    assert {r.normalized_type for r in model.rooms} == {RoomType.LIVING_ROOM, RoomType.BEDROOM}


def test_layer0_wall_fallback(tmp_path: Path) -> None:
    """Homeowner/legacy DXFs drawing walls purely on layer '0' must ingest via fallback."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    for a, b in [
        ((0, 0), (6000, 0)),
        ((6000, 0), (6000, 4000)),
        ((6000, 4000), (0, 4000)),
        ((0, 4000), (0, 0)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": "0"})
    msp.add_text("LIVING ROOM", dxfattribs={"layer": "room_names", "insert": (3000, 2000)})

    dxf_path = tmp_path / "layer0_home.dxf"
    doc.saveas(dxf_path)

    model, flags = ingest_dxf(dxf_path, building_type=BuildingType.INDEPENDENT_HOUSE)
    assert "WALLS_INFERRED_FROM_LAYER_0" in {f.code for f in flags}
    assert len(model.rooms) == 1
    assert model.rooms[0].normalized_type == RoomType.LIVING_ROOM
    assert 23.0 <= model.total_floor_area_m2 <= 25.0


def test_doorless_partition_gaps_morphologically_bridged(tmp_path: Path) -> None:
    """Doorless interior partition walls with 900 mm doorway gaps must separate distinct rooms."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # 8000 x 4000 mm shell split at x=4000 with a 900 mm doorless gap (y=1550..2450)
    for a, b in [
        ((0, 0), (8000, 0)),
        ((8000, 0), (8000, 4000)),
        ((8000, 4000), (0, 4000)),
        ((0, 4000), (0, 0)),
        ((4000, 0), (4000, 1550)),
        ((4000, 2450), (4000, 4000)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
    msp.add_text("BEDROOM 1", dxfattribs={"layer": "A-TEXT", "insert": (2000, 2000)})
    msp.add_text("BEDROOM 2", dxfattribs={"layer": "A-TEXT", "insert": (6000, 2000)})

    dxf_path = tmp_path / "doorless_two_beds.dxf"
    doc.saveas(dxf_path)

    model, flags = ingest_dxf(dxf_path, building_type=BuildingType.APARTMENT_2BED)
    assert "DOORWAY_GAPS_MORPHOLOGICALLY_BRIDGED" in {f.code for f in flags}
    assert len(model.rooms) == 2
    assert {r.normalized_type for r in model.rooms} == {
        RoomType.MASTER_BEDROOM,
        RoomType.BEDROOM,
    }


def test_overlong_circuits_auto_split_for_practicability() -> None:
    """Phase 2 pipeline must auto-split rings >120 m and lighting >95 m to preserve sizing."""
    from cyelec.models.electrical import CircuitCategory

    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(building)
    flag_codes = {f.code for f in p2.design.review_flags}
    assert "CIRCUIT_AUTO_SPLIT_FOR_PRACTICABILITY" in flag_codes

    for circ in p2.design.circuits:
        if circ.spec.category == CircuitCategory.LIGHTING:
            assert circ.cable_mm2 <= 2.5
        if circ.spec.category == CircuitCategory.SOCKET_RING:
            assert circ.spec.length_m <= 120.0


