"""End-to-end Phase 2 test: DXF ingest -> wet zones -> placement -> NetworkX -> BS 7671 solve."""

from __future__ import annotations

from pathlib import Path

import pytest
from ezdxf.filemanagement import readfile as read_dxf

from cyelec.models.electrical import EacProfile, EarthingSystem
from cyelec.models.spatial import SpatialLayoutResult
from cyelec.pipeline import export_phase2_deliverables, run_phase2_pipeline
from cyelec.render.layout_dxf import ELECTRICAL_LAYERS
from cyelec.synth.sample_plan import generate_cyprus_3bed_dxf


def test_phase2_dxf_to_deliverables_end_to_end(tmp_path: Path) -> None:
    input_dxf = tmp_path / "input_cyprus_3bed.dxf"
    gt = generate_cyprus_3bed_dxf(out_dxf=input_dxf)

    out_dir = tmp_path / "out_phase2"
    p2 = run_phase2_pipeline(input_dxf)
    outputs = export_phase2_deliverables(p2, out_dir)

    # 1. Ingestion + provenance
    assert p2.design.provenance.input_sha256 == gt.source_sha256
    assert p2.design.building.total_floor_area_m2 == pytest.approx(120.0, abs=0.05)
    assert p2.design.earthing_system == EarthingSystem.TT
    assert p2.design.eac_compliance.eac_profile == EacProfile.EAC_18TH_A2
    assert p2.design.has_blockers is False
    assert all(c.overall_pass for c in p2.design.circuits)

    # 2. True NetworkX routed cable lengths fed directly into BS 7671 circuit specs
    routed_map = {rc.circuit_id: rc.total_routed_length_m for rc in p2.spatial.routed_circuits}
    for solved in p2.design.circuits:
        assert solved.spec.length_m == pytest.approx(routed_map[solved.spec.circuit_id], abs=0.01)

    # 3. Phase balance passes <= 10%
    assert p2.design.phase_balance is not None
    assert p2.design.phase_balance.within_target is True

    # 4. Spatial JSON snapshot round-trips cleanly
    rehydrated_spatial = SpatialLayoutResult.model_validate_json(
        outputs["spatial_json"].read_text(encoding="utf-8")
    )
    assert len(rehydrated_spatial.wet_zones) == 2  # Bathroom (bathtub) + En-Suite (shower tray)
    assert rehydrated_spatial.total_conduit_m > 100.0

    # 5. Annotated electrical_layout.dxf passes ezdxf audit with populated E-* layers
    layout_doc = read_dxf(str(outputs["layout_dxf"]))
    auditor = layout_doc.audit()
    assert len(auditor.errors) == 0
    assert layout_doc.dxfversion == "AC1032"
    used_layers = {
        ent.dxf.layer
        for layout in layout_doc.layouts
        for ent in layout
    }
    for layer in ELECTRICAL_LAYERS:
        assert layer in layout_doc.layers
        assert layer in used_layers

    # 6. All Phase 1 tabular/report deliverables also emitted
    for key in ("json", "xlsx", "csv", "md", "html", "spatial_json", "layout_dxf"):
        assert outputs[key].is_file()
