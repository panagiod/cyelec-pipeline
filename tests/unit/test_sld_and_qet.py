"""Unit tests for deterministic SLD (SVG/DXF) and isolated QElectroTech subprocess bridge."""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from ezdxf.filemanagement import readfile as read_dxf

from cyelec.models.electrical import ReviewSeverity
from cyelec.pipeline import run_phase1_pipeline
from cyelec.render.qet_bridge import (
    export_qet_project,
    render_qet_via_subprocess,
)
from cyelec.render.sld import SLD_DXF_LAYERS, export_sld_deliverables
from cyelec.synth.sample_plan import build_cyprus_3bed_model


def test_sld_svg_and_dxf_rendering_and_audit(tmp_path: Path) -> None:
    design = run_phase1_pipeline(build_cyprus_3bed_model())
    svg_path, dxf_path = export_sld_deliverables(
        design,
        tmp_path / "sld.svg",
        tmp_path / "sld.dxf",
    )

    # 1. SVG is valid XML containing all circuits, EAC supply tier, TT earthing, and ETEK block
    svg_text = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(svg_text)
    assert root.tag.endswith("svg")
    assert "SINGLE-LINE DIAGRAM" in svg_text
    assert "EAC kWh METER" in svg_text
    assert "TT EARTH ROD" in svg_text
    assert "STATUTORY ETEK ELECTRICAL ENGINEER SIGN-OFF" in svg_text
    for circ in design.circuits:
        assert circ.spec.circuit_id in svg_text

    # 2. DXF passes ezdxf audit with 0 errors and all standardized E-SLD-* layers populated
    doc = read_dxf(str(dxf_path))
    auditor = doc.audit()
    assert len(auditor.errors) == 0
    assert doc.dxfversion == "AC1032"
    assert doc.header["$INSUNITS"] == 4
    used_layers = {ent.dxf.layer for ent in doc.modelspace()}
    for layer in SLD_DXF_LAYERS:
        assert layer in doc.layers
        assert layer in used_layers


def test_qet_xml_export_and_isolated_subprocess_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = run_phase1_pipeline(build_cyprus_3bed_model())
    qet_path = export_qet_project(design, tmp_path / "sld.qet")

    # Valid QElectroTech XML project structure
    root = ET.fromstring(qet_path.read_text(encoding="utf-8"))
    assert root.tag == "project"
    assert root.find("diagram") is not None
    assert root.find("diagram/shapes") is not None
    assert root.find("diagram/inputs") is not None

    # 1. Missing binary degrades gracefully with INFO flag
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    status_missing = render_qet_via_subprocess(qet_path, tmp_path / "out_missing")
    assert status_missing.qet_binary_available is False
    assert status_missing.rendered_output_path is None
    assert status_missing.flag is not None
    assert status_missing.flag.code == "QET_SUBPROCESS_SKIPPED"
    assert status_missing.flag.severity == ReviewSeverity.INFO

    # 2. Present binary runs strictly in isolated subprocess with QT_QPA_PLATFORM=offscreen
    captured: dict[str, Any] = {}

    def fake_run(
        cmd: list[str],
        *,
        check: bool,
        timeout: int,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env_qt"] = env.get("QT_QPA_PLATFORM")
        out_dir = Path(cmd[cmd.index("--export-dir") + 1])
        (out_dir / f"{qet_path.stem}.svg").write_text("<svg/>", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/qelectrotech")
    monkeypatch.setattr(subprocess, "run", fake_run)

    out_ok = tmp_path / "out_ok"
    status_ok = render_qet_via_subprocess(qet_path, out_ok, fmt="svg")
    assert status_ok.qet_binary_available is True
    assert status_ok.rendered_output_path == out_ok / "sld.svg"
    assert captured["env_qt"] == "offscreen"
    assert captured["cmd"][0] == "/usr/bin/qelectrotech"
