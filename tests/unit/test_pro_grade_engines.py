"""Analytical cross-check & engineering invariant tests for the 5 Pro-Grade Redesign Pillars."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.ingest.messy_cad_recovery import (
    bridge_nearby_wall_endpoints,
    detect_loose_door_swings,
    extract_parallel_double_wall_lines,
    strip_mtext_formatting,
)
from cyelec.ingest.pdf_vector_ingest import ingest_vector_pdf
from cyelec.models.building import BuildingModel, Point2D, Room, RoomType
from cyelec.models.electrical import CircuitCategory, CircuitSpec, CircuitTopology
from cyelec.models.spatial import ConduitSegment, DeviceType, PlacedDevice, RoutedCircuit
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.label_layout import LabelLayoutEngine, count_svg_text_bbox_overlaps
from cyelec.render.selectivity_svg import render_selectivity_curves_svg
from cyelec.solver.network_solver import (
    compute_radial_distributed_metrics,
    compute_ring_kirchhoff_metrics,
)
from cyelec.solver.photometrics import (
    direct_illuminance_point_lux,
    evaluate_room_photometrics_2d,
)
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


# =============================================================================
# Pillar 1: Distributed Multi-Node Tree, Kirchhoff Ring Final & IEC 60909 Tests
# =============================================================================
def test_uniform_straight_radial_closed_form_vd_factor() -> None:
    """Verify exact (N+1)/(2N) distributed voltage-drop moment on a uniform N-load radial."""
    n_loads = 4
    step_mm = 5000.0  # 5 m between equal luminaires along straight 20 m corridor
    db = Point2D(x_mm=0.0, y_mm=0.0)
    segs: list[ConduitSegment] = []
    devs: list[PlacedDevice] = []
    prev = db
    for k in range(1, n_loads + 1):
        pt = Point2D(x_mm=k * step_mm, y_mm=0.0)
        segs.append(ConduitSegment(start_mm=prev, end_mm=pt, length_m=5.0, edge_key=f"E{k}"))
        devs.append(
            PlacedDevice(
                device_id=f"L{k}",
                room_id="R1",
                circuit_id="C1",
                device_type=DeviceType.LUMINAIRE,
                label=f"L{k}",
                location_mm=pt,
                mounting_height_mm=2900.0,
                luminaire_wattage_w=15.0,
            )
        )
        prev = pt

    route = RoutedCircuit(
        circuit_id="C1",
        segments=segs,
        horizontal_length_m=20.0,
        vertical_drops_m=2.0,
        total_routed_length_m=22.0,
    )
    spec = CircuitSpec(
        circuit_id="C1",
        description="Test Uniform Radial",
        category=CircuitCategory.LIGHTING,
        topology=CircuitTopology.RADIAL,
        connected_load_w=60.0,
        diversity_factor=1.0,
        diversified_load_w=60.0,
        design_current_ib_a=4.0,
        length_m=22.0,
    )
    # Use ceiling_height_m=1.6 so DB riser = 0.5m and ceiling drop = 0.25m (minimal vertical offset)
    m = compute_radial_distributed_metrics(spec, db, route, devs, ceiling_height_m=1.6)
    # Exact closed-form analytical factor including 0.5m riser & 0.25m final drop:
    # M = Ib*0.5 + Ib*(5 + 0.75*5 + 0.50*5 + 0.25*5) + 0.25*Ib*0.25 = Ib * 13.0625
    # L_elec = 0.5 + 20.0 + 0.25 = 20.75 m -> exact ratio = 13.0625 / 20.75 = 0.62952
    expected_factor = (0.5 + 5.0 * (1.0 + 0.75 + 0.50 + 0.25) + 0.25 * 0.25) / 20.75
    assert abs(m.distributed_vd_factor - expected_factor) / expected_factor < 0.005
    assert m.worst_case_node_id == "L4"


def test_kirchhoff_ring_final_single_midpoint_and_uniform_limits() -> None:
    """Verify exact Kirchhoff ring limits: single midpoint (/4) & uniform distributed (/8)."""
    db = Point2D(x_mm=0.0, y_mm=0.0)
    spec = CircuitSpec(
        circuit_id="C3",
        description="Test 32A Ring Final",
        category=CircuitCategory.SOCKET_RING,
        topology=CircuitTopology.RING_FINAL,
        connected_load_w=7360.0,
        diversity_factor=1.0,
        diversified_load_w=7360.0,
        design_current_ib_a=32.0,
        length_m=40.0,
    )
    route = RoutedCircuit(
        circuit_id="C3",
        segments=[],
        horizontal_length_m=36.0,
        vertical_drops_m=4.0,
        total_routed_length_m=40.0,
    )

    # Limit 1: Single concentrated load at midpoint -> L_elec = L/4 = 10.0m, factor = 1.00
    mid_dev = [
        PlacedDevice(
            device_id="S-MID",
            room_id="R1",
            circuit_id="C3",
            device_type=DeviceType.SOCKET_TWIN,
            label="Mid Socket",
            location_mm=Point2D(x_mm=10000.0, y_mm=0.0),
            mounting_height_mm=450.0,
        )
    ]
    m_mid = compute_ring_kirchhoff_metrics(spec, db, route, mid_dev)
    assert abs(m_mid.electrical_path_length_m - 10.0) < 0.05
    assert abs(m_mid.distributed_vd_factor - 1.0) < 0.005

    # Limit 2: Many evenly spaced sockets around a closed circle -> L_elec = L/4, factor -> 0.50
    n_ring = 20
    radius_mm = 40000.0 / (2.0 * math.pi)
    ring_devs = [
        PlacedDevice(
            device_id=f"S{k}",
            room_id="R1",
            circuit_id="C3",
            device_type=DeviceType.SOCKET_TWIN,
            label=f"S{k}",
            location_mm=Point2D(
                x_mm=round(radius_mm * (1.0 - math.cos(2.0 * math.pi * k / (n_ring + 1))), 1),
                y_mm=round(radius_mm * math.sin(2.0 * math.pi * k / (n_ring + 1)), 1),
            ),
            mounting_height_mm=450.0,
        )
        for k in range(1, n_ring + 1)
    ]
    m_unif = compute_ring_kirchhoff_metrics(spec, db, route, ring_devs)
    assert abs(m_unif.electrical_path_length_m - 10.0) <= 0.15
    assert abs(m_unif.distributed_vd_factor - 0.50) <= 0.03


def test_cyprus_outdoor_veranda_separated_and_iec60909_e203_generated() -> None:
    """Outdoor veranda sockets sit on dedicated 16A 2.5mm² radial; indoor rings stay 2.5mm²."""
    bldg = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(bldg)

    # Indoor socket rings must stay authentic BS 1363 2.5/2.5 mm² (never 4.0 mm²)
    rings = [c for c in p2.design.circuits if c.spec.topology == CircuitTopology.RING_FINAL]
    assert rings, "Expected at least one indoor 32A ring final"
    for rc in rings:
        assert rc.cable_mm2 == 2.5, f"Indoor ring {rc.spec.circuit_id} upsized to {rc.cable_mm2}"

    # Cooker CPC follows authentic BS 7671 Table 54.7 (6/4 mm², not 6/6 mm²)
    cookers = [c for c in p2.design.circuits if c.spec.category == CircuitCategory.COOKER]
    if cookers:
        assert cookers[0].cable_mm2 == 6.0
        assert cookers[0].cpc_mm2 == 4.0

    # IEC 60909 fault profile, vendor catalog ref & E-203 SVG
    assert p2.design.selectivity_analysis is not None
    assert p2.design.selectivity_analysis.all_breaking_capacity_pass is True
    for c in p2.design.circuits:
        assert c.ik1_min_a is not None and c.ik1_min_a > 0.0
        assert c.magnetic_trip_max_a is not None and c.magnetic_trip_max_a > 0.0
        assert c.vendor_catalog_ref != ""

    e203_svg = render_selectivity_curves_svg(p2.design)
    assert "<svg" in e203_svg and "E-203 TIME-CURRENT" in e203_svg
    assert "Ik3_max" in e203_svg and "BS 88" in e203_svg


# =============================================================================
# Pillar 2: AutoCAD Drafting Quality & Hard `svg_text_bbox_overlaps == 0` Gate
# =============================================================================
def test_label_layout_engine_stress_zero_overlaps() -> None:
    """25 tags stacked at identical (x, y) must resolve to 0 overlaps with leader callouts."""
    eng = LabelLayoutEngine((0.0, 0.0, 900.0, 600.0))
    boxes = []
    leaders = 0
    for i in range(25):
        p = eng.place_point_tag(450.0, 300.0, f"TAG-{i:02d}(a)", font_px=6.6, bold=True)
        boxes.append(p.bbox)
        if p.needs_leader:
            leaders += 1
    assert leaders > 0, "Dense cluster should trigger clean leader callouts"
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            overlap = not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
            assert not overlap, f"Overlap between placed tag {i} and {j}: {a} vs {b}"


@pytest.mark.parametrize("template", list(BenchmarkTemplate))
def test_floorplan_svg_strict_zero_text_overlaps(template: BenchmarkTemplate) -> None:
    """Hard drafting invariant: zero <text> bbox overlaps across E-100..E-103 + Heatmap."""
    bldg = generate_benchmark_dxf(template)
    p2 = run_phase2_pipeline(bldg)
    for disc in ("combined", "lighting", "power", "elv"):
        svg = render_floorplan_svg(p2, discipline=disc)  # type: ignore[arg-type]
        assert count_svg_text_bbox_overlaps(svg) == 0, (
            f"Text bbox overlap detected on {template.value} / {disc}"
        )
    hm_svg = render_floorplan_svg(p2, discipline="lighting", show_lux_heatmap=True)
    assert 'class="lux-heatmap"' in hm_svg
    assert count_svg_text_bbox_overlaps(hm_svg) == 0


# =============================================================================
# Pillar 3: Point-by-Point 2D Working-Plane Photometrics Analytical Tests
# =============================================================================
def test_photometrics_inverse_square_cosine_cubed_analytical_accuracy() -> None:
    """Verify closed-form inverse-square cosine-cubed illuminance to <0.1%."""
    phi_m = 2000.0  # maintained lumens
    h = 2.0  # m above working plane
    # For Lambertian downlight n=1: I0 = Phi_m / pi; E(r=0) = I0 / h^2 = Phi_m / (pi * h^2)
    e_nadir = direct_illuminance_point_lux(phi_m, 0.0, h, beam_exponent_n=1.0)
    expected_nadir = phi_m / (math.pi * h * h)
    assert abs(e_nadir - expected_nadir) / expected_nadir < 1e-4

    # At r = h (45 deg): cos^4(45 deg) = 0.25 -> E(r=h) = 0.25 * E(0)
    e_45 = direct_illuminance_point_lux(phi_m, h, h, beam_exponent_n=1.0)
    assert abs(e_45 - 0.25 * expected_nadir) / (0.25 * expected_nadir) < 1e-4


def test_photometrics_2d_room_grid_and_uniformity_u0() -> None:
    """Verify 2D room grid outputs E_min <= E_avg <= E_max and valid U0 = E_min / E_avg."""
    room = Room(
        id="R1",
        raw_label="Test Office",
        normalized_type=RoomType.OPEN_OFFICE,
        polygon_mm=[
            Point2D(x_mm=0.0, y_mm=0.0),
            Point2D(x_mm=6000.0, y_mm=0.0),
            Point2D(x_mm=6000.0, y_mm=4000.0),
            Point2D(x_mm=0.0, y_mm=4000.0),
        ],
        area_m2=24.0,
        ceiling_height_mm=2900.0,
    )
    lums = [
        PlacedDevice(
            device_id=f"L{i}",
            room_id="R1",
            device_type=DeviceType.LUMINAIRE,
            label=f"Panel {i}",
            location_mm=Point2D(x_mm=x, y_mm=y),
            mounting_height_mm=2900.0,
            luminaire_wattage_w=40.0,
            lumens=4200,
        )
        for i, (x, y) in enumerate(
            [(1500.0, 1000.0), (4500.0, 1000.0), (1500.0, 3000.0), (4500.0, 3000.0)],
            start=1,
        )
    ]
    res = evaluate_room_photometrics_2d(room, lums, grid_step_mm=250.0)
    assert res.grid_points >= 250
    assert res.min_lux <= res.avg_lux <= res.max_lux
    assert abs(res.uniformity_u0 - round(res.min_lux / res.avg_lux, 2)) <= 0.01
    assert res.u0_target == 0.60
    assert res.u0_compliant is True


# =============================================================================
# Pillar 4: Messy CAD Recovery & Direct Vector Architectural PDF Ingestion Tests
# =============================================================================
def test_messy_cad_mtext_stripping_double_walls_and_loose_doors() -> None:
    """Verify MTEXT control stripping, Greek homoglyphs, double-wall pairing & loose doors."""
    raw = r"{\fArial|b1|i0;\H1.2x;Υπνοδωµάτιo\P(15.4 m²)}"
    assert strip_mtext_formatting(raw) == "Υπνοδωμάτιο (15.4 m²)"

    # Double-line 200mm masonry walls mixed with short diagonal hatch/furniture clutter
    walls_200mm = [
        (Point2D(x_mm=0, y_mm=0), Point2D(x_mm=4000, y_mm=0)),
        (Point2D(x_mm=0, y_mm=200), Point2D(x_mm=4000, y_mm=200)),
        (Point2D(x_mm=0, y_mm=3000), Point2D(x_mm=4000, y_mm=3000)),
        (Point2D(x_mm=0, y_mm=3200), Point2D(x_mm=4000, y_mm=3200)),
    ]
    clutter = [
        (Point2D(x_mm=1000, y_mm=1000), Point2D(x_mm=1250, y_mm=1150)),
        (Point2D(x_mm=2000, y_mm=1200), Point2D(x_mm=2400, y_mm=1800)),
    ]
    recovered = extract_parallel_double_wall_lines(walls_200mm + clutter)
    assert len(recovered) == 4

    # Loose quarter-circle ARC + radial LINE door swing
    arcs = [(Point2D(x_mm=1000, y_mm=0), 850.0, 0.0, 90.0)]
    leaves = [(Point2D(x_mm=1000, y_mm=0), Point2D(x_mm=1000, y_mm=850))]
    doors = detect_loose_door_swings(arcs, leaves)
    assert len(doors) == 1
    assert doors[0].width_mm == 850.0

    # Micro gap bridging (60mm corner gap)
    gap_walls = [
        (Point2D(x_mm=0, y_mm=0), Point2D(x_mm=3000, y_mm=0)),
        (Point2D(x_mm=3060, y_mm=0), Point2D(x_mm=3060, y_mm=3000)),
    ]
    bridges = bridge_nearby_wall_endpoints(gap_walls, min_gap_mm=10.0, max_gap_mm=120.0)
    assert len(bridges) == 1


def _write_minimal_pdf(out_pdf: Path, stream_body: str) -> None:
    """Write a valid zero-dependency PDF 1.4 file with an A4 page and Helvetica font."""
    stream_bytes = stream_body.encode("latin-1")
    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        (
            b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>\nendobj\n"
        ),
        (
            f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        ),
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objs) + 1}\n".encode("latin-1"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    buf.extend(
        (
            f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )
    out_pdf.write_bytes(bytes(buf))


def _build_synthetic_vector_plan_pdf(out_pdf: Path) -> None:
    """Generate a 100% original zero-dep CAD-style vector architectural PDF (scale 1:100)."""
    # PDF user space: origin bottom-left (0,0), A4 595x842 pt. At 1:100, 1 pt = 35.278 mm.
    cmds: list[str] = ["0 0 0 RG", "1.0 w"]

    def _rect_lines(x0: float, y0: float, x1: float, y1: float, w: float = 0.9) -> None:
        cmds.append(f"{w:.2f} w")
        cmds.append(f"{x0:.1f} {y0:.1f} m {x1:.1f} {y0:.1f} l S")
        cmds.append(f"{x1:.1f} {y0:.1f} m {x1:.1f} {y1:.1f} l S")
        cmds.append(f"{x1:.1f} {y1:.1f} m {x0:.1f} {y1:.1f} l S")
        cmds.append(f"{x0:.1f} {y1:.1f} m {x0:.1f} {y0:.1f} l S")

    # Outer double-line walls (280 x 240 pt -> 9.88 m x 8.47 m = ~83 m²)
    _rect_lines(100, 450, 380, 690, 1.0)
    _rect_lines(106, 456, 374, 684, 0.9)
    # 3 rooms: Living/Kitchen (left), Bedroom (top-right), Bathroom (bottom-right)
    _rect_lines(106, 456, 250, 684, 0.7)
    _rect_lines(253, 570, 374, 684, 0.7)
    _rect_lines(253, 456, 374, 567, 0.7)
    # Additional structural wall ticks & double partitions to exceed >= 30 thick vector segments
    for k in range(5):
        y_t = 470 + k * 45
        cmds.append(f"0.60 w 250.0 {y_t:.1f} m 253.0 {y_t:.1f} l S")
        cmds.append(f"0.60 w 100.0 {y_t:.1f} m 106.0 {y_t:.1f} l S")
        cmds.append(f"0.60 w 374.0 {y_t:.1f} m 380.0 {y_t:.1f} l S")

    # Room labels + explicit scale 1:100
    cmds.extend(
        [
            "BT /F1 10 Tf 155 590 Td (Kitchen) Tj ET",
            "BT /F1 10 Tf 155 540 Td (Living Room) Tj ET",
            "BT /F1 10 Tf 285 625 Td (Bedroom 1) Tj ET",
            "BT /F1 10 Tf 285 510 Td (Bathroom) Tj ET",
            "BT /F1 11 Tf 110 410 Td (FLOOR PLAN SCALE 1:100) Tj ET",
        ]
    )
    _write_minimal_pdf(out_pdf, "\n".join(cmds))


def test_direct_vector_pdf_ingestion_and_api_preview(tmp_path: Path) -> None:
    """Verify end-to-end vector PDF ingestion + /v1/solve/dxf/preview + raster PDF HTTP 422."""
    vec_pdf = tmp_path / "synthetic_house_plan.pdf"
    _build_synthetic_vector_plan_pdf(vec_pdf)

    model, flags = ingest_vector_pdf(vec_pdf)
    assert isinstance(model, BuildingModel)
    assert len(model.rooms) >= 3
    room_types = {r.normalized_type for r in model.rooms}
    assert RoomType.KITCHEN_LIVING in room_types
    assert RoomType.BEDROOM in room_types
    assert RoomType.BATHROOM in room_types
    assert any(f.code == "PDF_VECTOR_INGESTION_SUMMARY" for f in flags)

    # Test directly via FastAPI /v1/solve/dxf/preview
    app = create_app(db_path=tmp_path / "api.db")
    client = TestClient(app)
    resp = client.post(
        "/v1/solve/dxf/preview",
        files={"file": ("synthetic_house_plan.pdf", vec_pdf.read_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload["result"]["design"]["circuits"]) >= 6
    assert "<svg" in payload["selectivity_svg"]
    assert 'class="lux-heatmap"' in payload["lighting_heatmap_svg"]
    assert count_svg_text_bbox_overlaps(payload["lighting_svg"]) == 0

    # Verify scanned raster-only PDF (zero CAD vector linework) is cleanly rejected with HTTP 422
    raster_pdf = tmp_path / "scanned_raster.pdf"
    _write_minimal_pdf(raster_pdf, "BT /F1 12 Tf 72 720 Td (Scanned bitmap page) Tj ET")
    with pytest.raises(ValueError, match="scanned raster|insufficient"):
        ingest_vector_pdf(raster_pdf)

    bad_resp = client.post(
        "/v1/solve/dxf/preview",
        files={"file": ("scanned_raster.pdf", raster_pdf.read_bytes(), "application/pdf")},
    )
    assert bad_resp.status_code == 422


def test_e203_selectivity_pdf_registered_in_deliverables_and_zip(tmp_path: Path) -> None:
    """E-203 PDF must be registered when export_svg_sheet_to_pdf returns (pdf_path, None)."""
    import zipfile
    from unittest.mock import patch

    from cyelec.render.eac_pack import build_submission_zip
    from cyelec.render.pdf_export import is_weasyprint_available
    from cyelec.render.selectivity_svg import export_selectivity_deliverables

    bldg = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(bldg)

    # Always verify the (pdf_path, None) success contract even without local WeasyPrint
    def _fake_svg_pdf(src: Path, dst: Path, **_kwargs: object) -> tuple[Path, None]:
        assert src.name == "E203_selectivity_curves.svg"
        dst.write_bytes(b"%PDF-1.7\n" + b"0" * 2048)
        return dst, None

    mock_dir = tmp_path / "mock_sel"
    with patch("cyelec.render.selectivity_svg.export_svg_sheet_to_pdf", side_effect=_fake_svg_pdf):
        outs = export_selectivity_deliverables(p2, mock_dir, export_pdf=True)
    assert "e203_selectivity_svg" in outs and outs["e203_selectivity_svg"].is_file()
    assert "e203_selectivity_pdf" in outs and outs["e203_selectivity_pdf"].is_file()
    assert outs["e203_selectivity_pdf"].read_bytes().startswith(b"%PDF-")

    zp = build_submission_zip(outs, tmp_path / "sel.zip")
    with zipfile.ZipFile(zp) as zf:
        assert "E203_selectivity_curves.pdf" in zf.namelist()

    # If WeasyPrint is installed in the test environment (e.g. Docker / Cloud Run), verify real PDF
    if is_weasyprint_available():
        real_outs = export_selectivity_deliverables(p2, tmp_path / "real_sel", export_pdf=True)
        assert "e203_selectivity_pdf" in real_outs
        raw_pdf = real_outs["e203_selectivity_pdf"].read_bytes()
        assert raw_pdf.startswith(b"%PDF-") and len(raw_pdf) > 1024


def test_uniformity_fill_downlights_refine_l_shaped_room_and_noop_when_compliant() -> None:
    """L-shaped room with dark leg gets fill downlights to reach U0 >= target; compliant is noop."""
    from cyelec.models.building import BuildingType, DistributionBoardAnchor
    from cyelec.spatial.placement import (
        _place_ceiling_luminaires,
        _plan_uniformity_fill_points,
    )

    # 1. Already-compliant rectangular office -> _plan_uniformity_fill_points is a no-op
    rect_room = Room(
        id="R_RECT",
        raw_label="Compliant Office",
        normalized_type=RoomType.OPEN_OFFICE,
        polygon_mm=[
            Point2D(x_mm=0.0, y_mm=0.0),
            Point2D(x_mm=6000.0, y_mm=0.0),
            Point2D(x_mm=6000.0, y_mm=4000.0),
            Point2D(x_mm=0.0, y_mm=4000.0),
        ],
        area_m2=24.0,
        ceiling_height_mm=2900.0,
    )
    rect_lums = [
        PlacedDevice(
            device_id=f"RL{i}",
            room_id="R_RECT",
            device_type=DeviceType.LUMINAIRE,
            label=f"Panel {i}",
            location_mm=Point2D(x_mm=x, y_mm=y),
            mounting_height_mm=2900.0,
            luminaire_wattage_w=40.0,
            lumens=4200,
        )
        for i, (x, y) in enumerate(
            [(1500.0, 1000.0), (4500.0, 1000.0), (1500.0, 3000.0), (4500.0, 3000.0)],
            start=1,
        )
    ]
    fills_noop, u0_b, u0_a = _plan_uniformity_fill_points(rect_room, rect_lums, max_extra=4)
    assert fills_noop == []
    assert u0_b == u0_a and u0_b >= 0.60

    # 2. L-shaped open-plan room whose naive bounding-box grid drops points in the narrow leg
    l_poly = [
        Point2D(x_mm=0.0, y_mm=0.0),
        Point2D(x_mm=8500.0, y_mm=0.0),
        Point2D(x_mm=8500.0, y_mm=3200.0),
        Point2D(x_mm=3400.0, y_mm=3200.0),
        Point2D(x_mm=3400.0, y_mm=7800.0),
        Point2D(x_mm=0.0, y_mm=7800.0),
    ]
    # Area = 8.5*3.2 + 3.4*4.6 = 27.2 + 15.64 = 42.84 m²
    l_room = Room(
        id="R_LOPEN",
        raw_label="Open Plan Living/Kitchen",
        normalized_type=RoomType.KITCHEN_LIVING,
        polygon_mm=l_poly,
        area_m2=42.84,
        ceiling_height_mm=2800.0,
    )
    # Verify naive grid layout (without fill) fails U0 < 0.40 on this geometry
    naive_pts = _place_ceiling_luminaires(l_room, count=6)
    naive_lums = [
        PlacedDevice(
            device_id=f"NL{i}",
            room_id="R_LOPEN",
            device_type=DeviceType.LUMINAIRE,
            label=f"L{i}",
            location_mm=pt,
            mounting_height_mm=2800.0,
            luminaire_wattage_w=14.0,
            lumens=1500,
        )
        for i, pt in enumerate(naive_pts, start=1)
    ]
    naive_res = evaluate_room_photometrics_2d(l_room, naive_lums, grid_step_mm=250.0)
    assert naive_res.u0_compliant is False, f"Expected naive L-shape to fail U0, got {naive_res}"

    # Full pipeline run must add fill downlights, emit INFO flag, and satisfy both U0 & E_avg
    model = BuildingModel(
        project_name="L-Shape Uniformity Test",
        building_type=BuildingType.APARTMENT_1BED,
        total_floor_area_m2=42.84,
        rooms=[l_room],
        db_anchor=DistributionBoardAnchor(
            id="DB-MAIN",
            location_mm=Point2D(x_mm=500.0, y_mm=500.0),
        ),
    )
    p2 = run_phase2_pipeline(model)
    assert all(c.overall_pass for c in p2.design.circuits)
    assert any(
        f.code == "PHOTOMETRIC_UNIFORMITY_FILL_ADDED" for f in p2.design.review_flags
    ), "Expected PHOTOMETRIC_UNIFORMITY_FILL_ADDED INFO flag"
    chk = next(lc for lc in p2.design.lumen_checks if lc.room_id == "R_LOPEN")
    assert chk.u0_compliant is True, f"Expected refined L-shape U0 compliant, got {chk}"
    assert chk.achieved_lux >= chk.target_lux * 0.90
    assert chk.achieved_lux <= chk.target_lux * 1.40

