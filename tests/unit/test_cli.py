"""Unit tests for the Typer CLI commands using CliRunner."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cyelec.cli import app

runner = CliRunner()


def test_cli_version_and_check_standards() -> None:
    res_v = runner.invoke(app, ["version"])
    assert res_v.exit_code == 0
    assert "cyelec-pipeline" in res_v.stdout

    res_s = runner.invoke(app, ["check-standards"])
    assert res_s.exit_code == 0
    assert "[OK] bs7671_cables" in res_s.stdout
    assert "[OK] eac_cyprus_rules" in res_s.stdout


def test_cli_synth_inspect_solve_eac_and_export_sld(tmp_path: Path) -> None:
    dxf_path = tmp_path / "plan.dxf"
    json_path = tmp_path / "plan.json"

    res_synth = runner.invoke(
        app,
        ["synth-plan", "--out", str(dxf_path), "--json", str(json_path)],
    )
    assert res_synth.exit_code == 0
    assert dxf_path.is_file()
    assert json_path.is_file()

    res_inspect = runner.invoke(app, ["inspect-dxf", str(dxf_path)])
    assert res_inspect.exit_code == 0
    assert "120.00 m²" in res_inspect.stdout

    p1_dir = tmp_path / "p1"
    res_p1 = runner.invoke(app, ["solve-synth", "--out-dir", str(p1_dir)])
    assert res_p1.exit_code == 0
    assert (p1_dir / "load_schedule.xlsx").is_file()

    p2_dir = tmp_path / "p2"
    res_p2 = runner.invoke(app, ["solve-dxf", str(dxf_path), "--out-dir", str(p2_dir)])
    assert res_p2.exit_code == 0
    assert (p2_dir / "electrical_layout.dxf").is_file()

    p3_dir = tmp_path / "p3"
    res_p3 = runner.invoke(app, ["solve-eac", str(dxf_path), "--out-dir", str(p3_dir)])
    assert res_p3.exit_code == 0
    assert (p3_dir / "eac_submission_pack.zip").is_file()
    assert (p3_dir / "floorplan_overlay.svg").is_file()

    sld_dir = tmp_path / "sld"
    res_sld = runner.invoke(
        app,
        ["export-sld", str(p3_dir / "design_result.json"), "--out-dir", str(sld_dir)],
    )
    assert res_sld.exit_code == 0
    assert (sld_dir / "single_line_diagram.svg").is_file()
    assert (sld_dir / "single_line_diagram.dxf").is_file()
    assert (sld_dir / "single_line_diagram.qet").is_file()


def test_cli_benchmark_suite(tmp_path: Path) -> None:
    res = runner.invoke(app, ["benchmark-suite", "--out-dir", str(tmp_path / "bench")])
    assert res.exit_code == 0
    assert "apartment_3bed" in res.stdout
    assert "villa_pv_ev" in res.stdout
    assert "commercial_office" in res.stdout
