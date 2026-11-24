"""Unit tests auditing the parametric synthetic 120 m² Cyprus 3-bed apartment R2018 DXF."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Polygon

from cyelec.synth.sample_plan import STANDARD_LAYERS, generate_cyprus_3bed_dxf


def test_synthetic_cyprus_3bed_dxf_audit_and_geometry(tmp_path: Path) -> None:
    dxf_path = tmp_path / "cyprus_3bed.dxf"
    model = generate_cyprus_3bed_dxf(out_dxf=dxf_path)

    assert dxf_path.is_file()
    assert model.source_sha256 is not None
    assert len(model.source_sha256) == 64
    assert model.total_floor_area_m2 == pytest.approx(120.0, abs=1e-4)

    # Verify non-overlapping Shapely polygons summing to exact 120.0 m²
    polys = [Polygon([(p.x_mm, p.y_mm) for p in r.polygon_mm]) for r in model.rooms]
    assert all(p.is_valid and p.area > 0 for p in polys)
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            assert polys[i].intersection(polys[j]).area == pytest.approx(0.0, abs=1e-3)
    assert sum(p.area for p in polys) / 1_000_000.0 == pytest.approx(120.0, abs=1e-4)

    # Verify bathtub + shower tray fixtures for BS 7671 Section 701 wet-zone testing
    fixture_types = {f.fixture_type for r in model.rooms for f in r.fixtures}
    assert "bathtub" in fixture_types
    assert "shower_tray" in fixture_types

    # Read back with ezdxf and run DXF auditor
    doc = ezdxf.readfile(str(dxf_path))
    auditor = doc.audit()
    assert len(auditor.errors) == 0
    assert doc.dxfversion == "AC1032"  # R2018
    assert doc.header["$INSUNITS"] == 4  # Millimeters

    # Verify all standardized architectural layers exist and are populated
    msp = doc.modelspace()
    used_layers = {entity.dxf.layer for entity in msp}
    for layer_name in STANDARD_LAYERS:
        assert layer_name in doc.layers
        assert layer_name in used_layers

    # Verify Greek + English labels on A-TEXT and swing arcs on A-DOOR
    mtext_entities = [e for e in msp.query("MTEXT") if e.dxf.layer == "A-TEXT"]
    all_labels = " ".join(e.plain_text() for e in mtext_entities)
    assert "ΣΑΛΟΝΙ" in all_labels
    assert "ΚΟΥΖΙΝΑ" in all_labels
    assert "ΜΠΑΝΙΟ" in all_labels
    assert "Living Room" in all_labels

    door_arcs = [e for e in msp.query("ARC") if e.dxf.layer == "A-DOOR"]
    assert len(door_arcs) == len(model.doors)
