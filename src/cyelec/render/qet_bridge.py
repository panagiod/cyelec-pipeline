"""Strictly isolated QElectroTech (0.200+ CLI, GPLv2+) .qet XML exporter and subprocess bridge.

Architectural boundary:
  QElectroTech is GPLv2+ and MUST NEVER be imported or linked into Python.
  This module only generates standalone .qet XML project files and optionally invokes
  the external `qelectrotech` binary as an isolated child process with QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cyelec.models.electrical import ElectricalDesignResult, ReviewFlag, ReviewSeverity


@dataclass(frozen=True)
class QetRenderStatus:
    """Result of attempting optional isolated QElectroTech CLI subprocess export."""

    qet_project_path: Path
    qet_binary_available: bool
    rendered_output_path: Path | None
    message: str
    flag: ReviewFlag | None = None


def build_qet_xml(result: ElectricalDesignResult) -> str:
    """Generate a deterministic QElectroTech 0.9/0.200+ compatible .qet XML project."""
    eac = result.eac_compliance
    prov = result.provenance

    project = ET.Element(
        "project",
        attrib={
            "version": "0.90",
            "title": f"SLD - {result.building.project_name}",
        },
    )

    props = ET.SubElement(project, "properties")
    for key, val in [
        ("eac_profile", eac.eac_profile.value),
        ("earthing_system", result.earthing_system.value),
        ("supply_tier", eac.recommended_supply_tier),
        ("pipeline_version", prov.pipeline_version),
        ("input_sha256", prov.input_sha256),
    ]:
        p_el = ET.SubElement(props, "property", attrib={"name": key, "show": "1"})
        p_el.text = val

    ET.SubElement(project, "newdiagrams")

    diagram = ET.SubElement(
        project,
        "diagram",
        attrib={
            "order": "1",
            "title": f"{result.building.db_anchor.id} Single-Line Diagram",
            "author": "cyelec-pipeline (ETEK Sign-Off Required)",
            "date": prov.timestamp_utc[:10],
            "cols": "17",
            "colsize": "60",
            "rows": "8",
            "rowsize": "80",
            "height": "640",
            "displaycols": "true",
            "displayrows": "true",
            "folio": "%id/%total",
        },
    )

    inputs = ET.SubElement(diagram, "inputs")
    # Header text annotation inside QET folio
    ET.SubElement(
        inputs,
        "input",
        attrib={
            "x": "40",
            "y": "30",
            "text": (
                f"EAC {eac.recommended_supply_tier} | "
                f"{result.earthing_system.value} (Ze/Ra={result.ze_or_ra_ohm:.1f} Ohm)"
            ),
        },
    )

    shapes = ET.SubElement(diagram, "shapes")
    # Main busbar line
    bus_x0 = 60
    bus_x1 = max(920, 60 + len(result.circuits) * 70)
    ET.SubElement(
        shapes,
        "shape",
        attrib={
            "type": "Line",
            "x1": str(bus_x0),
            "y1": "140",
            "x2": str(bus_x1),
            "y2": "140",
        },
    )

    # Circuit branch lines and device labels
    for idx, circ in enumerate(result.circuits):
        s = circ.spec
        x = bus_x0 + 35 + idx * 70
        ET.SubElement(
            shapes,
            "shape",
            attrib={
                "type": "Line",
                "x1": str(x),
                "y1": "140",
                "x2": str(x),
                "y2": "340",
            },
        )
        ET.SubElement(
            inputs,
            "input",
            attrib={
                "x": str(x - 24),
                "y": "165",
                "text": f"{s.circuit_id} ({circ.phase.value})",
            },
        )
        ET.SubElement(
            inputs,
            "input",
            attrib={
                "x": str(x - 24),
                "y": "205",
                "text": f"{circ.breaker_in_a}A {circ.breaker_curve.value}",
            },
        )
        ET.SubElement(
            inputs,
            "input",
            attrib={
                "x": str(x - 24),
                "y": "245",
                "text": f"{circ.cable_mm2:g}/{circ.cpc_mm2:g}mm2",
            },
        )
        ET.SubElement(
            inputs,
            "input",
            attrib={
                "x": str(x - 24),
                "y": "285",
                "text": f"L={s.length_m:.0f}m dV={circ.voltage_drop_pct:.1f}%",
            },
        )

    ET.indent(project, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(project, encoding="unicode")


def export_qet_project(result: ElectricalDesignResult, out_qet: Path) -> Path:
    """Write standalone QElectroTech .qet XML project file."""
    out_qet.parent.mkdir(parents=True, exist_ok=True)
    out_qet.write_text(build_qet_xml(result), encoding="utf-8")
    return out_qet


def is_qelectrotech_available() -> bool:
    """Return True if the external `qelectrotech` CLI binary is present on PATH."""
    return shutil.which("qelectrotech") is not None


def render_qet_via_subprocess(
    qet_path: Path,
    out_dir: Path,
    *,
    fmt: Literal["svg", "pdf"] = "svg",
    timeout_s: int = 60,
) -> QetRenderStatus:
    """Invoke external GPLv2+ QElectroTech CLI in an isolated offscreen subprocess if installed."""
    binary = shutil.which("qelectrotech")
    if binary is None:
        return QetRenderStatus(
            qet_project_path=qet_path,
            qet_binary_available=False,
            rendered_output_path=None,
            message=(
                "External `qelectrotech` binary not installed; generated editable .qet XML "
                "and used built-in deterministic SVG/DXF SLD renderer."
            ),
            flag=ReviewFlag(
                code="QET_SUBPROCESS_SKIPPED",
                severity=ReviewSeverity.INFO,
                category="geometry",
                message=(
                    "Optional external QElectroTech CLI not present on PATH; editable "
                    f"'{qet_path.name}' exported alongside built-in deterministic SVG/DXF SLD."
                ),
                requires_etek_signoff=False,
            ),
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"

    cmd = [
        binary,
        "--export-format",
        fmt,
        "--export-dir",
        str(out_dir),
        str(qet_path),
    ]
    subprocess.run(cmd, check=True, timeout=timeout_s, env=env, capture_output=True, text=True)

    rendered = out_dir / f"{qet_path.stem}.{fmt}"
    return QetRenderStatus(
        qet_project_path=qet_path,
        qet_binary_available=True,
        rendered_output_path=rendered if rendered.is_file() else None,
        message=f"Rendered QElectroTech project via isolated subprocess ({fmt}).",
    )
