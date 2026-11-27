"""MIT-original regression tests for real Cyprus architectural floorplan & EAC 2026 patterns."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from ezdxf.filemanagement import new as new_dxf
from shapely.geometry import Point, Polygon

from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.models.building import BuildingType, Room, RoomType
from cyelec.models.electrical import (
    DesignOverrides,
    EacProfile,
    EarthingSystem,
    ResidentialSystemsBrief,
)
from cyelec.models.spatial import DeviceType
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline


def _build_cyprus_house_dxf(path: Path) -> Path:
    """Create a synthetic Cyprus residential DXF testing all real-world drafting patterns."""
    doc = new_dxf("R2018")
    doc.header["$INSUNITS"] = 4
    for layer in ("A-WALL", "A-DOOR", "A-GLAZ", "A-ROOM-NAME", "A-SANITARY", "A-FURN", "A-ANNO"):
        doc.layers.add(layer)
    msp = doc.modelspace()

    def poly(pts: list[tuple[float, float]], layer: str = "A-WALL") -> None:
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})

    def txt(val: str, x: float, y: float, layer: str = "A-ROOM-NAME") -> None:
        msp.add_text(val, dxfattribs={"layer": layer, "height": 180.0, "insert": (x, y)})

    # 1. Master Bedroom + Secondary Bedroom (both with A-FURN polyline+text beds)
    poly([(0, 5600), (4000, 5600), (4000, 9200), (0, 9200)])
    txt("υπνοδωμάτιο 1", 1500, 7400)
    poly([(300, 7000), (1900, 7000), (1900, 9000), (300, 9000)], "A-FURN")
    txt("BED", 1000, 8000, "A-FURN")
    msp.add_arc((4000, 5900), 900, 90, 180, dxfattribs={"layer": "A-DOOR"})

    poly([(6400, 5600), (10000, 5600), (10000, 9200), (6400, 9200)])
    txt("υπνοδωμάτιο 2", 7700, 7400)
    poly([(8000, 7000), (9600, 7000), (9600, 9000), (8000, 9000)], "A-FURN")
    txt("BED", 8700, 8000, "A-FURN")
    msp.add_arc((6400, 5900), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})

    # 2. UNLABELLED BATHROOM (bathtub + WC + basin, ZERO text label!)
    poly([(4000, 6800), (6400, 6800), (6400, 9200), (4000, 9200)])
    poly([(4100, 8350), (5800, 8350), (5800, 9100), (4100, 9100)], "A-SANITARY")
    txt("BATH", 4900, 8700, "A-SANITARY")
    poly([(4150, 6950), (4550, 6950), (4550, 7550), (4150, 7550)], "A-SANITARY")
    txt("WC", 4350, 7250, "A-SANITARY")
    poly([(5700, 6950), (6250, 6950), (6250, 7400), (5700, 7400)], "A-SANITARY")
    txt("BASIN", 5950, 7150, "A-SANITARY")
    msp.add_arc((4700, 6800), 800, 0, 90, dxfattribs={"layer": "A-DOOR"})

    # 3. UNLABELLED INTERIOR CORRIDOR HUB (4 doors touching perimeter, ZERO text label!)
    poly([(4000, 5600), (6400, 5600), (6400, 6800), (4000, 6800)])
    msp.add_arc((4700, 5600), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})

    # 4. Open-Plan Greek Living + Dining + Kitchen with Ψ (fridge) & ΠΛ (washer) callouts
    poly([(0, 1600), (10000, 1600), (10000, 5600), (0, 5600)])
    txt("καθιστικό", 2000, 3600)
    txt("τραπεζαρία", 5200, 3600)
    txt("κουζίνα", 8200, 3600)
    txt("Ψ", 9500, 5100, "A-ANNO")
    txt("ΠΛ", 9500, 2600, "A-ANNO")
    msp.add_arc((2400, 1600), 1000, 0, 90, dxfattribs={"layer": "A-DOOR"})
    msp.add_arc((8000, 1600), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})

    # 5. LEADER-pointed Covered & Uncovered Verandas (text OUTSIDE polygon!)
    poly([(0, 0), (5200, 0), (5200, 1600), (0, 1600)])
    msp.add_leader([(2600, 700), (2600, -700)], dxfattribs={"layer": "A-ANNO"})
    txt("καλυμμένη βεράντα", 2600, -700)

    poly([(5200, 0), (10000, 0), (10000, 1600), (5200, 1600)])
    msp.add_leader([(7600, 700), (7600, -700)], dxfattribs={"layer": "A-ANNO"})
    txt("ακάλυπτη βεράντα", 7600, -700)

    doc.saveas(path)
    return path


def test_cyprus_real_plan_unlabelled_wet_room_leader_verandas_and_two_way_switching(
    tmp_path: Path,
) -> None:
    dxf_path = _build_cyprus_house_dxf(tmp_path / "cyprus_real_patterns.dxf")
    res = run_phase2_pipeline(
        dxf_path,
        overrides=DesignOverrides(
            earthing_system=EarthingSystem.TN_C_S,
            eac_profile=EacProfile.EAC_17TH_A3,
            pv_capacity_kva=6.0,
            residential_brief=ResidentialSystemsBrief(ev_charger_kw=11.0),
        ),
        building_type=BuildingType.INDEPENDENT_HOUSE,
        project_name="Cyprus Real Pattern House",
    )

    rooms_by_type: dict[RoomType, list[Room]] = {}
    for rm in res.design.building.rooms:
        rooms_by_type.setdefault(rm.normalized_type, []).append(rm)

    # 1. Unlabelled bathroom MUST be promoted from STORAGE -> BATHROOM with full Sec. 701 safety
    assert RoomType.BATHROOM in rooms_by_type
    bath = rooms_by_type[RoomType.BATHROOM][0]
    assert bath.is_wet_room is True
    assert "inferred" in bath.raw_label.lower()
    assert any(f.code == "WET_ROOM_INFERRED_FROM_FIXTURES" for f in res.design.review_flags)

    # 2. Unlabelled 4-door circulation hub promoted -> CORRIDOR
    assert RoomType.CORRIDOR in rooms_by_type
    assert any(f.code == "CORRIDOR_INFERRED_FROM_DOORS" for f in res.design.review_flags)

    # 3. Both LEADER-pointed verandas (covered & uncovered) classified -> VERANDA (outdoor IP65)
    verandas = rooms_by_type.get(RoomType.VERANDA, [])
    assert len(verandas) == 2
    assert all(v.is_outdoor and v.ambient_temp_c == 45 for v in verandas)

    # 4. Open-plan room preserves authentic Greek labels & captures 'Ψ' as fridge furniture
    kl = rooms_by_type[RoomType.KITCHEN_LIVING][0]
    assert "καθιστικό" in kl.raw_label and "κουζίνα" in kl.raw_label
    assert any(fn.furniture_type == "fridge" for fn in kl.furniture)

    # 5. Secondary bedroom with bed gets 2-way αλέ-ρετούρ switching + sockets strictly on room wall
    bed2 = rooms_by_type[RoomType.BEDROOM][0]
    bed2_devs = [d for d in res.spatial.devices if d.room_id == bed2.id]
    bed2_switches = [d for d in bed2_devs if d.device_type == DeviceType.SWITCH]
    assert [s.switch_type for s in bed2_switches] == ["1G2W", "1G2W"]
    bed2_poly = Polygon([(p.x_mm, p.y_mm) for p in bed2.polygon_mm])
    for sock in [d for d in bed2_devs if d.device_type == DeviceType.SOCKET_TWIN]:
        assert bed2_poly.exterior.distance(Point(sock.location_mm.x_mm, sock.location_mm.y_mm)) < 5

    # 6. Export and verify Official 2026 HMY 58.18-1 + EAC PVS-S-1.2 SLD compliance tokens
    out_dir = tmp_path / "export"
    export_phase3_deliverables(res, out_dir, export_pdf=False, source_dxf_path=dxf_path)

    eac_html = (out_dir / "eac_application_form.html").read_text(encoding="utf-8")
    for token in (
        "Ε-ΔΔ-707",
        "Η.Μ.Υ. 58.18-1",
        "ΗΜΥ 58",
        "ΕΠΟΜΕΝΗ ΕΠΙΘΕΩΡΗΣΗ",
        "ΚΩΔΙΚΕΣ ΓΙΑ ΤΟΝ ΤΥΠΟ ΣΥΡΜΑΤΩΣΗΣ",
        "Αρ. Σημείων",
        "Iimp",
        "Uc",
        "0.5 Ω",
        "1.0 Ω",
        "ΠΙΝΑΚΑΣ Β",
        "Ε-ΔΔ-709",
        "Ε-ΔΔ-744",
    ):
        assert token in eac_html, f"Missing official 2026 EAC/HMY token: {token}"

    sld_svg = (out_dir / "single_line_diagram.svg").read_text(encoding="utf-8")
    for token in (
        "EAC BIDIRECTIONAL METER",
        "EAC R1 CURTAILMENT + R2 ZERO-EXPORT RX",
        "PVS-S-1.2",
        "Uc=275V",
        "SPD TYPE 2 (40kA)",
    ):
        assert token in sld_svg, f"Missing official 2026 EAC SLD token: {token}"


def test_raw_unlayered_pdf_vector_dump_fails_fast_without_hanging(tmp_path: Path) -> None:
    """Dense unlayered layer-0 raw vector dumps (e.g. PDF->DXF) must fail fast, never hang GEOS."""
    doc = new_dxf("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    # Build a dense 12x12 grid (144 cells = >40 unlabelled cells, 0 texts, pure layer '0')
    step = 2000.0
    for i in range(13):
        msp.add_line((0.0, i * step), (12.0 * step, i * step), dxfattribs={"layer": "0"})
        msp.add_line((i * step, 0.0), (i * step, 12.0 * step), dxfattribs={"layer": "0"})
    # Plus 800 short noise hatch segments simulating PDF stroke noise
    for k in range(800):
        x = (k % 40) * 150.0
        y = (k // 40) * 150.0
        msp.add_line((x, y), (x + 80.0, y + 60.0), dxfattribs={"layer": "0"})
    dxf_path = tmp_path / "raw_pdf_vector_soup.dxf"
    doc.saveas(dxf_path)

    t0 = time.perf_counter()
    with pytest.raises(ValueError, match="unlayered raw vector/PDF export"):
        ingest_dxf(dxf_path)
    elapsed = time.perf_counter() - t0
    assert elapsed < 12.0, f"Raw dense DXF guard took {elapsed:.2f}s (expected < 12s)"
