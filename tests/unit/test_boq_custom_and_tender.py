"""Tests for Track C: per-tenant custom BOQ rate cards & unpriced Tender BOQ export mode."""

from __future__ import annotations

import csv
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from typer.testing import CliRunner

from cyelec.api.app import create_app
from cyelec.cli import app as cli_app
from cyelec.models.electrical import BoqRateOverrides, DesignOverrides
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_custom_boq_rate_overrides_and_reduced_vat() -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    base_res = run_phase2_pipeline(building)
    assert base_res.design.boq is not None
    base_total = base_res.design.boq.grand_total_eur
    assert base_res.design.boq.vat_rate == 0.19

    # Apply custom contractor rate card + Cyprus 5% reduced primary-residence VAT
    custom_rates = BoqRateOverrides(
        cable_eur_per_m={"2.5": 5.00, "4": 7.50},
        switchgear_eur={"rcbo_30ma_1p": 55.00, "main_board_3p": 520.00},
        accessories_eur={"twin_13a_socket": 40.00},
        vat_rate=0.05,
    )
    custom_res = run_phase2_pipeline(
        building,
        overrides=DesignOverrides(boq_rate_overrides=custom_rates),
    )
    boq = custom_res.design.boq
    assert boq is not None
    assert not boq.is_tender_blank_rates
    assert "+tenant custom rates" in boq.rates_version
    assert boq.vat_rate == 0.05
    assert boq.grand_total_eur != base_total

    # Verify individual overridden unit rates took effect
    rates_by_desc = {it.description_en: it.unit_rate_eur for it in boq.items}
    assert any(
        "2.5 mm²" in desc and rate == 5.00 for desc, rate in rates_by_desc.items()
    )
    assert any(
        "RCBO 1P+N 30 mA" in desc and rate == 55.00 for desc, rate in rates_by_desc.items()
    )
    assert any(
        "Twin 13 A Switched Socket" in desc and rate == 40.00
        for desc, rate in rates_by_desc.items()
    )
    assert abs(boq.vat_eur - round(boq.subtotal_eur * 0.05, 2)) < 1e-6


def test_tenant_saved_boq_rate_card_inherited_and_isolated(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "boq_saas.db"))

    # Tenant A saves a custom BOQ rate card in their tenant profile
    put_resp = client.put(
        "/v1/tenants/me",
        json={
            "display_name": "Paphos Premium MEP",
            "boq_rate_overrides": {
                "switchgear_eur": {"main_board_3p": 999.0},
                "accessories_eur": {"luminaire_point": 80.0},
                "vat_rate": 0.19,
            },
        },
        headers={"X-Tenant-ID": "tenant-paphos"},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["boq_rate_overrides"]["switchgear_eur"]["main_board_3p"] == 999.0

    # Tenant A runs benchmark with NO request-level overrides -> inherits saved custom rates
    solve_a = client.post(
        "/v1/solve/benchmark/apartment_3bed",
        headers={"X-Tenant-ID": "tenant-paphos"},
    )
    assert solve_a.status_code == 200, solve_a.text
    boq_a = solve_a.json()["result"]["design"]["boq"]
    assert "+tenant custom rates" in boq_a["rates_version"]
    assert any(
        "Main Distribution Board" in item["description_en"] and item["unit_rate_eur"] == 999.0
        for item in boq_a["items"]
    )

    # Tenant B remains strictly isolated and uses standard Cyprus default rates
    solve_b = client.post(
        "/v1/solve/benchmark/apartment_3bed",
        headers={"X-Tenant-ID": "tenant-nicosia"},
    )
    assert solve_b.status_code == 200, solve_b.text
    boq_b = solve_b.json()["result"]["design"]["boq"]
    assert "+tenant custom rates" not in boq_b["rates_version"]
    assert boq_a["grand_total_eur"] > boq_b["grand_total_eur"]


def test_tender_boq_blank_rates_excel_formulas_csv_and_cli(tmp_path: Path) -> None:
    dxf_path = tmp_path / "apt.dxf"
    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=dxf_path)

    # 1. Direct pipeline + export in Tender BOQ blank-rate mode
    p2 = run_phase2_pipeline(
        building,
        overrides=DesignOverrides(tender_boq_blank_rates=True),
    )
    boq = p2.design.boq
    assert boq is not None
    assert boq.is_tender_blank_rates is True
    assert boq.grand_total_eur == 0.0
    assert all(it.unit_rate_eur == 0.0 and it.total_eur == 0.0 for it in boq.items)
    assert all(it.quantity > 0.0 for it in boq.items)

    out_dir = tmp_path / "tender_pack"
    outputs = export_phase3_deliverables(p2, out_dir)

    # 2. Verify Excel Sheet 3 has blank Col G cells AND live auto-calc formulas in Col H
    wb = load_workbook(str(outputs["xlsx"]), data_only=False)
    ws3 = wb["Bill of Quantities"]
    assert "TENDER BILL OF QUANTITIES" in str(ws3["A1"].value)
    first_row = 5
    last_row = first_row + len(boq.items) - 1
    for r in range(first_row, last_row + 1):
        assert ws3.cell(row=r, column=6).value > 0  # Qty preserved
        assert ws3.cell(row=r, column=7).value is None  # Unit Rate intentionally blank!
        assert ws3.cell(row=r, column=8).value == f"=ROUND(F{r}*G{r},2)"

    sub_row = last_row + 2
    vat_row = sub_row + 1
    tot_row = vat_row + 1
    assert ws3.cell(row=sub_row, column=8).value == f"=SUM(H{first_row}:H{last_row})"
    assert ws3.cell(row=vat_row, column=8).value == f"=ROUND(H{sub_row}*0.19,2)"
    assert ws3.cell(row=tot_row, column=8).value == f"=H{sub_row}+H{vat_row}"

    # 3. Verify boq.csv leaves Unit Rate & Total empty while keeping Quantities
    with outputs["boq_csv"].open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    item_rows = [r for r in rows[1:] if r[0].startswith("BOQ-")]
    assert len(item_rows) == len(boq.items)
    for r in item_rows:
        assert float(r[5]) > 0.0
        assert r[6] == ""
        assert r[7] == ""
    assert rows[-1][2] == "GRAND TOTAL (EUR incl. VAT)"
    assert rows[-1][7] == ""

    # 4. CLI `cyelec solve-eac --tender-boq`
    runner = CliRunner()
    cli_out = tmp_path / "cli_tender"
    cli_res = runner.invoke(
        cli_app,
        ["solve-eac", str(dxf_path), "--out-dir", str(cli_out), "--tender-boq", "--no-zip"],
    )
    assert cli_res.exit_code == 0, cli_res.output
    assert "TENDER (UNPRICED)" in cli_res.output
