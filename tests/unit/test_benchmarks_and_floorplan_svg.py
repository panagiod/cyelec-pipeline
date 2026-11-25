"""Unit tests for Cyprus Villa PV+EV, Commercial Office benchmarks, and Floorplan SVG renderer."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from ezdxf.filemanagement import readfile as read_dxf

from cyelec.models.electrical import CircuitCategory, ReviewSeverity
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.synth.benchmarks import (
    BenchmarkTemplate,
    build_cyprus_commercial_office_model,
    generate_benchmark_dxf,
)


def test_cyprus_villa_pv_ev_benchmark_and_floorplan_svg(tmp_path: Path) -> None:
    dxf_path = tmp_path / "villa.dxf"
    villa = generate_benchmark_dxf(BenchmarkTemplate.VILLA_PV_EV, out_dxf=dxf_path)

    assert villa.total_floor_area_m2 == pytest.approx(216.0, abs=0.05)
    assert villa.pv_capacity_kva == pytest.approx(7.5)
    assert villa.ev_charger_kw == pytest.approx(7.4)

    # Audited DXF output
    doc = read_dxf(str(dxf_path))
    assert len(doc.audit().errors) == 0

    p2 = run_phase2_pipeline(villa)
    categories = [c.spec.category for c in p2.design.circuits]

    # Villa > 100 m² general area splits into >= 2 general rings (+ 1 kitchen ring = 3 rings)
    assert categories.count(CircuitCategory.SOCKET_RING) >= 3
    # Dedicated 7.4 kW EV Charger Mode 3 circuit present
    assert CircuitCategory.EV_CHARGER in categories
    # PV > 5 kVA mandatory 3-phase flag emitted
    assert any(f.code == "EAC_PV_THREE_PHASE_MANDATORY" for f in p2.design.review_flags)
    assert p2.design.has_blockers is False
    assert all(c.overall_pass for c in p2.design.circuits)

    # Floorplan SVG parses cleanly as valid XML with bilingual labels & legend
    fp_svg = render_floorplan_svg(p2)
    root = ET.fromstring(fp_svg)
    assert root.tag.endswith("svg")
    assert "ELECTRICAL LAYOUT" in fp_svg
    assert "ΣΑΛΟΝΙ" in fp_svg
    assert "Sec 701 Zone 0 / Zone 1" in fp_svg


def test_cyprus_commercial_office_triggers_ct_metering_and_substation_escalation() -> None:
    # 1. Default 95 kVA extra 3P load -> total diversified >= 100 kVA & > 100 A/ph
    office = build_cyprus_commercial_office_model()
    p2_warn = run_phase2_pipeline(office)
    eac_w = p2_warn.design.eac_compliance

    assert eac_w.supply_phases == 3
    assert eac_w.ct_metering_required is True
    assert eac_w.substation_warning is True
    assert eac_w.substation_consultation_required is False
    assert any(
        f.code == "EAC_CT_METERING_REQUIRED" and f.severity == ReviewSeverity.WARNING
        for f in p2_warn.design.review_flags
    )
    assert any(
        f.code == "EAC_SUBSTATION_WARNING" and f.severity == ReviewSeverity.WARNING
        for f in p2_warn.design.review_flags
    )

    # 2. High-load office (145 kVA extra 3P load -> >= 150 kVA total) -> Substation BLOCKER
    office_big = build_cyprus_commercial_office_model(extra_three_phase_load_kva=145.0)
    p2_blk = run_phase2_pipeline(office_big)
    assert p2_blk.design.eac_compliance.substation_consultation_required is True
    assert p2_blk.design.has_blockers is True
    assert any(
        f.code == "EAC_SUBSTATION_CONSULTATION_BLOCKER" and f.severity == ReviewSeverity.BLOCKER
        for f in p2_blk.design.review_flags
    )
