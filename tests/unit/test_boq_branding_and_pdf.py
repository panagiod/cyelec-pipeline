"""Tests for Pillar 3: Cyprus BOQ (EUR + 19% VAT), Tenant Branding, Bilingual EN/EL & PDF."""

from __future__ import annotations

from pathlib import Path

from ezdxf.filemanagement import readfile as read_dxf
from openpyxl import load_workbook

from cyelec.models.electrical import (
    DesignOverrides,
    ReportLanguage,
    ReviewSeverity,
    TenantBranding,
)
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.pdf_export import export_html_to_pdf, is_weasyprint_available
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_cyprus_boq_excel_formulas_and_vat_math(tmp_path: Path) -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.VILLA_PV_EV)
    branding = TenantBranding(
        firm_name="Kypros MEP Consulting Ltd",
        engineer_name="N. Papadopoulos",
        etek_reg_no="ETEK-E-4821",
        phone="+357 22 000000",
        email="eng@kyprosmep.cy",
    )
    overrides = DesignOverrides(
        report_language=ReportLanguage.BILINGUAL,
        branding=branding,
    )
    p2 = run_phase2_pipeline(building, overrides=overrides)
    boq = p2.design.boq
    assert boq is not None
    assert boq.currency == "EUR"
    assert boq.vat_rate == 0.19
    assert len(boq.items) >= 10

    # Verify exact subtotal, 19% Cyprus VAT, and grand total math
    expected_sub = round(sum(i.total_eur for i in boq.items), 2)
    assert boq.subtotal_eur == expected_sub
    assert boq.vat_eur == round(expected_sub * 0.19, 2)
    assert boq.grand_total_eur == round(boq.subtotal_eur + boq.vat_eur, 2)
    assert boq.grand_total_eur > 2000.0

    # EV charger & TT earth rod kit must appear in BOQ for Villa PV+EV
    descs = " ".join(i.description_en for i in boq.items)
    assert "EV Charger" in descs
    assert "TT Earth Electrode Kit" in descs

    # Export full Phase 3 pack and audit Sheet 3 Excel formulas + boq.csv + bilingual deliverables
    outputs = export_phase3_deliverables(p2, tmp_path / "pack", export_pdf=True)
    assert outputs["boq_csv"].is_file()
    boq_csv_txt = outputs["boq_csv"].read_text(encoding="utf-8")
    assert "GRAND TOTAL (EUR incl. VAT)" in boq_csv_txt
    assert f"{boq.grand_total_eur:.2f}" in boq_csv_txt

    wb = load_workbook(outputs["xlsx"], data_only=False)
    assert "Bill of Quantities" in wb.sheetnames
    ws3 = wb["Bill of Quantities"]
    # First line item formula =ROUND(F5*G5,2)
    assert str(ws3["H5"].value) == "=ROUND(F5*G5,2)"
    formulas_col_h = [
        str(ws3.cell(row=r, column=8).value or "") for r in range(1, ws3.max_row + 1)
    ]
    assert any(f.startswith("=SUM(H5:") for f in formulas_col_h)
    assert any("*0.19,2)" in f for f in formulas_col_h)

    # Bilingual EN/EL + Branding in Markdown, HTML, SLD SVG, EAC Cover, and Layout DXF
    md_text = outputs["md"].read_text(encoding="utf-8")
    assert "ΜΕΛΕΤΗ ΗΛΕΚΤΡΟΛΟΓΙΚΗΣ ΕΓΚΑΤΑΣΤΑΣΗΣ" in md_text
    assert "Kypros MEP Consulting Ltd" in md_text
    assert "ETEK-E-4821" in md_text
    assert "ΠΡΟΜΕΤΡΗΣΗ & ΚΟΣΤΟΛΟΓΗΣΗ" in md_text

    html_text = outputs["html"].read_text(encoding="utf-8")
    assert "ΜΕΛΕΤΗ ΗΛΕΚΤΡΟΛΟΓΙΚΗΣ ΕΓΚΑΤΑΣΤΑΣΗΣ" in html_text
    assert "N. Papadopoulos" in html_text
    assert "ETEK-E-4821" in html_text

    sld_svg = outputs["sld_svg"].read_text(encoding="utf-8")
    assert "ΜΟΝΟΓΡΑΜΜΙΚΟ ΔΙΑΓΡΑΜΜΑ" in sld_svg
    assert "Kypros MEP Consulting Ltd" in sld_svg
    assert "ETEK-E-4821" in sld_svg

    pack_html = outputs["eac_pack_html"].read_text(encoding="utf-8")
    assert "ΦΑΚΕΛΟΣ ΥΠΟΒΟΛΗΣ ΣΧΕΔΙΩΝ ΑΗΚ" in pack_html
    assert "Indicative BOQ Total" in pack_html

    dxf_doc = read_dxf(str(outputs["layout_dxf"]))
    title_texts = [
        str(e.dxf.text)
        for layout in dxf_doc.layouts
        for e in layout
        if e.dxftype() == "TEXT" and e.dxf.layer == "E-TITLE"
    ]
    assert any("Kypros MEP Consulting Ltd" in t for t in title_texts)
    assert any("ETEK-E-4821" in t for t in title_texts)


def test_greek_only_language_and_graceful_pdf_bridge(tmp_path: Path) -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(
        building,
        overrides=DesignOverrides(report_language=ReportLanguage.EL),
    )
    outputs = export_phase3_deliverables(p2, tmp_path / "el_pack")
    md_text = outputs["md"].read_text(encoding="utf-8")
    assert md_text.startswith("# ΜΕΛΕΤΗ ΗΛΕΚΤΡΟΛΟΓΙΚΗΣ ΕΓΚΑΤΑΣΤΑΣΗΣ")

    # PDF bridge returns either a valid PDF (if weasyprint installed) or graceful INFO flag
    pdf_path, flag = export_html_to_pdf(outputs["html"], tmp_path / "out.pdf")
    if is_weasyprint_available():
        assert pdf_path is not None and pdf_path.is_file()
        assert flag is None
    else:
        assert pdf_path is None
        assert flag is not None
        assert flag.code == "PDF_EXPORT_SKIPPED"
        assert flag.severity == ReviewSeverity.INFO
