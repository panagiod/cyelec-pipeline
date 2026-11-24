"""Unit tests for DXF ingestion (round-trip, unit scaling) and optional IFC mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
from ezdxf.filemanagement import new as new_dxf

from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.ingest.ifc_ingest import (
    IfcFixtureRecord,
    IfcSpaceRecord,
    build_model_from_ifc_records,
    ingest_ifc,
)
from cyelec.models.building import BuildingType, Point2D, RoomType
from cyelec.models.electrical import ReviewSeverity
from cyelec.synth.sample_plan import generate_cyprus_3bed_dxf


def test_dxf_ingest_round_trip_cyprus_3bed(tmp_path: Path) -> None:
    dxf_path = tmp_path / "cyprus_3bed.dxf"
    ground_truth = generate_cyprus_3bed_dxf(out_dxf=dxf_path)

    ingested, flags = ingest_dxf(dxf_path)

    assert ingested.source_sha256 == ground_truth.source_sha256
    assert ingested.building_type == BuildingType.APARTMENT_3BED_PLUS
    assert len(ingested.rooms) == len(ground_truth.rooms) == 11
    assert ingested.total_floor_area_m2 == pytest.approx(120.0, abs=0.05)
    assert len(ingested.doors) == len(ground_truth.doors) == 11
    assert len(ingested.windows) == len(ground_truth.windows) == 7

    # Exact room type inventory preserved
    gt_types = sorted(r.normalized_type.value for r in ground_truth.rooms)
    ing_types = sorted(r.normalized_type.value for r in ingested.rooms)
    assert ing_types == gt_types

    # Bathtub & shower tray extracted and bound to wet rooms
    ing_fixtures = {f.fixture_type for r in ingested.rooms for f in r.fixtures}
    assert "bathtub" in ing_fixtures
    assert "shower_tray" in ing_fixtures

    # DB1 recovered accurately
    assert ingested.db_anchor.location_mm.x_mm == pytest.approx(
        ground_truth.db_anchor.location_mm.x_mm, abs=5.0
    )
    assert ingested.db_anchor.location_mm.y_mm == pytest.approx(
        ground_truth.db_anchor.location_mm.y_mm, abs=5.0
    )
    assert not any(f.severity == ReviewSeverity.BLOCKER for f in flags)


def test_dxf_ingest_meters_and_unitless_scaling(tmp_path: Path) -> None:
    # 1. Meters ($INSUNITS=6): 12.0 x 10.0 = 120.0 m²
    doc_m = new_dxf("R2018", setup=True)
    doc_m.header["$INSUNITS"] = 6
    msp = doc_m.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (12.0, 0), (12.0, 10.0), (0, 10.0)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp.add_text("ΣΑΛΟΝΙ", dxfattribs={"layer": "A-TEXT", "height": 0.3}).set_placement((5.0, 5.0))
    m_path = tmp_path / "meters.dxf"
    doc_m.saveas(str(m_path))

    model_m, flags_m = ingest_dxf(m_path)
    assert model_m.total_floor_area_m2 == pytest.approx(120.0, abs=0.01)
    assert model_m.rooms[0].normalized_type == RoomType.LIVING_ROOM
    assert not any(f.code == "DXF_UNITLESS_INFERRED" for f in flags_m)

    # 2. Unitless ($INSUNITS=0) with metre-scale geometry -> inferred + warning flag
    doc_u = new_dxf("R2018", setup=True)
    doc_u.header["$INSUNITS"] = 0
    msp_u = doc_u.modelspace()
    msp_u.add_lwpolyline(
        [(0, 0), (8.0, 0), (8.0, 5.0), (0, 5.0)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp_u.add_text(
        "Kitchen", dxfattribs={"layer": "A-TEXT", "height": 0.25}
    ).set_placement((3.0, 2.5))
    u_path = tmp_path / "unitless.dxf"
    doc_u.saveas(str(u_path))

    model_u, flags_u = ingest_dxf(u_path)
    assert model_u.total_floor_area_m2 == pytest.approx(40.0, abs=0.01)
    assert any(f.code == "DXF_UNITLESS_INFERRED" for f in flags_u)
    assert any(f.code == "DB_ANCHOR_INFERRED" for f in flags_u)


def test_ifc_record_mapping_and_optional_import_guard(tmp_path: Path) -> None:
    spaces = [
        IfcSpaceRecord(
            global_id="SP1",
            name="ΣΑΛΟΝΙ",
            polygon_mm=[
                Point2D(x_mm=0, y_mm=0),
                Point2D(x_mm=5000, y_mm=0),
                Point2D(x_mm=5000, y_mm=4000),
                Point2D(x_mm=0, y_mm=4000),
            ],
        ),
        IfcSpaceRecord(
            global_id="SP2",
            name="ΜΠΑΝΙΟ",
            polygon_mm=[
                Point2D(x_mm=5000, y_mm=0),
                Point2D(x_mm=7500, y_mm=0),
                Point2D(x_mm=7500, y_mm=4000),
                Point2D(x_mm=5000, y_mm=4000),
            ],
        ),
    ]
    fixtures = [
        IfcFixtureRecord(
            global_id="FX1",
            predefined_type="BATH",
            polygon_mm=[
                Point2D(x_mm=6700, y_mm=500),
                Point2D(x_mm=7500, y_mm=500),
                Point2D(x_mm=7500, y_mm=2200),
                Point2D(x_mm=6700, y_mm=2200),
            ],
        )
    ]

    model, flags = build_model_from_ifc_records(spaces=spaces, fixtures=fixtures)
    assert model.total_floor_area_m2 == pytest.approx(30.0)
    assert model.rooms[0].normalized_type == RoomType.LIVING_ROOM
    assert model.rooms[1].normalized_type == RoomType.BATHROOM
    assert model.rooms[1].fixtures[0].fixture_type == "bathtub"
    assert any(f.code == "DB_ANCHOR_INFERRED" for f in flags)

    # Without optional [bim] extra installed, ingest_ifc gives clear installation guidance
    with pytest.raises(ImportError, match=r"cyelec-pipeline\[bim\]"):
        ingest_ifc(tmp_path / "dummy.ifc")
