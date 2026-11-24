"""End-to-end Phase 1 integration tests for Excel formulas, CSV, JSON, reports, and overrides."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import load_workbook

from cyelec.models.electrical import (
    CircuitOverride,
    DesignOverrides,
    EacProfile,
    EarthingSystem,
    ElectricalDesignResult,
    PhaseAssignment,
)
from cyelec.pipeline import export_phase1_deliverables, run_phase1_pipeline
from cyelec.render.schedule_xlsx import SCHEDULE_HEADERS
from cyelec.synth.sample_plan import generate_cyprus_3bed_dxf


def test_phase1_end_to_end_deliverables(tmp_path: Path) -> None:
    dxf_path = tmp_path / "cyprus_3bed_120m2.dxf"
    building = generate_cyprus_3bed_dxf(out_dxf=dxf_path)

    result = run_phase1_pipeline(building)
    outputs = export_phase1_deliverables(result, tmp_path)

    # Core engineering assertions
    assert result.has_blockers is False
    assert result.etek_signoff_required is True
    assert result.earthing_system == EarthingSystem.TT
    assert result.eac_compliance.supply_phases == 3
    assert result.eac_compliance.mandatory_three_phase is True
    assert result.phase_balance is not None
    assert result.phase_balance.within_target is True
    assert result.phase_balance.imbalance_pct <= 10.0
    assert all(c.overall_pass for c in result.circuits)
    assert any(f.code == "ETEK_SIGN_OFF_REQUIRED" for f in result.review_flags)

    # 1. JSON snapshot round-trip validation
    json_text = outputs["json"].read_text(encoding="utf-8")
    rehydrated = ElectricalDesignResult.model_validate_json(json_text)
    assert rehydrated.provenance.input_sha256 == building.source_sha256
    assert len(rehydrated.circuits) == len(result.circuits)

    # 2. CSV export verification
    with outputs["csv"].open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == SCHEDULE_HEADERS
    assert len(rows) == len(result.circuits) + 1

    # 3. Formula-backed Excel workbook verification
    wb = load_workbook(str(outputs["xlsx"]), data_only=False)
    assert wb.sheetnames == ["Load Schedule", "EAC & Review Flags"]

    ws = wb["Load Schedule"]
    # Check live Excel formulas on first circuit row (row 9)
    assert str(ws["H9"].value).startswith("=F9*G9")
    assert str(ws["J9"].value).startswith("=IF(")
    assert str(ws["W9"].value).startswith("=V9*S9*T9*U9")
    assert str(ws["Y9"].value).startswith("=IF(")
    assert str(ws["AA9"].value).startswith("=IF(")
    assert str(ws["AB9"].value).startswith("=IF(")
    assert str(ws["AD9"].value).startswith("=IF(")
    assert str(ws["AG9"].value).startswith("=IF(")
    assert str(ws["AI9"].value).startswith("=IF(")
    assert str(ws["AJ9"].value).startswith("=IF(AND(")

    # Check SUM, SUMIF, MAX, MIN, and imbalance formulas exist in sheet
    all_formulas = [
        str(cell.value)
        for row in ws.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=")
    ]
    assert any("=SUM(" in f for f in all_formulas)
    assert any("=SUMIF(" in f for f in all_formulas)
    assert any("=MAX(" in f for f in all_formulas)
    assert any("=MIN(" in f for f in all_formulas)

    # 4. Markdown & print-ready A4-landscape HTML reports
    md_text = outputs["md"].read_text(encoding="utf-8")
    html_text = outputs["html"].read_text(encoding="utf-8")
    assert "ETEK Electrical Engineer" in md_text
    assert "ETEK_SIGN_OFF_REQUIRED" in md_text
    assert "@page { size: A4 landscape;" in html_text
    assert "ΣΑΛΟΝΙ" in html_text
    assert "ETEK Electrical Engineer Sign-Off" in html_text


def test_phase1_pass2_engineer_overrides_reconciliation(tmp_path: Path) -> None:
    """Stateless Pass-2 reconciliation accepts DesignOverrides cleanly."""
    building = generate_cyprus_3bed_dxf(out_dxf=tmp_path / "rev2.dxf")
    overrides = DesignOverrides(
        eac_profile=EacProfile.EAC_17TH_A3,
        earthing_system=EarthingSystem.TN_S,
        ze_or_ra_ohm=0.50,
        pv_capacity_kva=7.5,
        circuit_overrides={
            "C3": CircuitOverride(
                circuit_id="C3",
                phase=PhaseAssignment.L2,
                grouping_count=2,
            )
        },
        notes="Pass-2 engineer revision: TN-S supply with 7.5 kVA PV and grouped kitchen ring.",
    )
    res = run_phase1_pipeline(building, overrides=overrides)

    assert res.provenance.overrides_applied is True
    assert res.eac_compliance.eac_profile == EacProfile.EAC_17TH_A3
    assert res.eac_compliance.bathroom_socket_exclusion_m == 3.0
    assert res.earthing_system == EarthingSystem.TN_S
    c3 = next(c for c in res.circuits if c.spec.circuit_id == "C3")
    assert c3.phase == PhaseAssignment.L2
    assert c3.cable_mm2 == 4.0  # Upsized from 2.5 due to Cyprus 40°C + Cg=0.80 grouping
    assert any(f.code == "EAC_PV_THREE_PHASE_MANDATORY" for f in res.review_flags)
