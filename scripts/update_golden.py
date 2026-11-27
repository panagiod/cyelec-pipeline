#!/usr/bin/env python3
"""Deterministic Golden Baseline Snapshot Generator & Checker (0% Tolerance).

Run `python3 scripts/update_golden.py` ONLY when an authorized BS 7671, Cyprus EAC,
or BOQ rate change intentionally alters expected engineering outputs.
"""

from __future__ import annotations

import json
import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import ezdxf

from cyelec.models.electrical import DesignOverrides, ResidentialSystemsBrief
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "golden_baselines.json"

SCENARIOS: tuple[tuple[str, BenchmarkTemplate, DesignOverrides | None], ...] = (
    ("apartment_3bed", BenchmarkTemplate.APARTMENT_3BED, None),
    (
        "villa_pv_ev_vrv",
        BenchmarkTemplate.VILLA_PV_EV,
        DesignOverrides(
            residential_brief=ResidentialSystemsBrief(hvac_mode="vrv", vrv_outdoor_kw=7.5)
        ),
    ),
    ("villa_2storey", BenchmarkTemplate.VILLA_2STOREY, None),
    (
        "villa_2storey_turnkey",
        BenchmarkTemplate.VILLA_2STOREY,
        DesignOverrides(
            pv_capacity_kva=6.0,
            residential_brief=ResidentialSystemsBrief(
                hvac_mode="heat_pump_ufh",
                heat_pump_kw=6.0,
                include_pool=True,
                include_knx=True,
                ev_charger_kw=11.0,
                building_height_m=12.0,
            ),
        ),
    ),
    ("commercial_office", BenchmarkTemplate.COMMERCIAL_OFFICE, None),
)


def _count_stacked_devices_lt_100mm(result: Any) -> int:
    room_floor = {r.id: r.floor for r in result.design.building.rooms}
    devs = result.spatial.devices
    cnt = 0
    for i in range(len(devs)):
        di = devs[i]
        fi = room_floor.get(di.room_id, "")
        for j in range(i + 1, len(devs)):
            dj = devs[j]
            if room_floor.get(dj.room_id, "") != fi:
                continue
            dist = math.hypot(
                di.location_mm.x_mm - dj.location_mm.x_mm,
                di.location_mm.y_mm - dj.location_mm.y_mm,
            )
            if dist < 100.0:
                cnt += 1
    return cnt


def _count_e201_subboard_overlaps(sld_svg: str) -> int:
    root = ET.fromstring(sld_svg)
    ns = "{http://www.w3.org/2000/svg}"
    boxes: list[tuple[float, float, float, float]] = []
    for rect in root.iter(f"{ns}rect"):
        y = float(rect.attrib.get("y", "0"))
        h = float(rect.attrib.get("height", "0"))
        if rect.attrib.get("rx") == "4" and y > 280 and h in (24.0, 44.0):
            x = float(rect.attrib["x"])
            w = float(rect.attrib["width"])
            boxes.append((x, y, x + w, y + h))
    overlaps = 0
    for i in range(len(boxes)):
        ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            bx0, by0, bx1, by1 = boxes[j]
            if not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0):
                overlaps += 1
    return overlaps


def compute_scenario_snapshot(
    name: str,
    template: BenchmarkTemplate,
    overrides: DesignOverrides | None,
) -> dict[str, Any]:
    """Run full Phase 2 + Phase 3 deliverables and capture exact deterministic metrics."""
    with tempfile.TemporaryDirectory(prefix=f"cyelec_golden_{name}_") as td:
        tdir = Path(td)
        dxf_path = tdir / f"{template.value}.dxf"
        pack_dir = tdir / "pack"
        generate_benchmark_dxf(template, out_dxf=dxf_path)
        p2 = run_phase2_pipeline(dxf_path, overrides=overrides)
        outputs = export_phase3_deliverables(p2, pack_dir, source_dxf_path=dxf_path)

        dxf_audit_errors = 0
        for dxf_out in sorted(pack_dir.glob("*.dxf")):
            doc = ezdxf.readfile(str(dxf_out))
            dxf_audit_errors += len(doc.audit().errors)

        sld_svg = outputs["e201_sld_svg"].read_text(encoding="utf-8")
        e301_svg = outputs["e301_details_svg"].read_text(encoding="utf-8")
        eac_html = (pack_dir / "eac_application_form.html").read_text(encoding="utf-8")

        d = p2.design
        sp = p2.spatial
        eac = d.eac_compliance
        pb = d.phase_balance
        boq = d.boq

        circuits_sig = [
            {
                "circuit_id": c.spec.circuit_id,
                "board_id": c.spec.board_id,
                "category": c.spec.category.value,
                "phase": c.phase.value,
                "breaker_in_a": c.breaker_in_a,
                "breaker_curve": c.breaker_curve.value,
                "cable_mm2": c.cable_mm2,
                "cpc_mm2": c.cpc_mm2,
                "length_m": round(c.spec.length_m, 2),
                "voltage_drop_pct": round(c.voltage_drop_pct, 2),
                "overall_pass": c.overall_pass,
            }
            for c in d.circuits
        ]

        sub_mains_sig = [
            {
                "board_id": sm.board_id,
                "parent_board_id": sm.parent_board_id,
                "floor": sm.floor,
                "breaker_in_a": sm.breaker_in_a,
                "cable_mm2": sm.cable_mm2,
                "cpc_mm2": sm.cpc_mm2,
                "voltage_drop_pct": round(sm.voltage_drop_pct, 2),
                "overall_pass": sm.overall_pass,
            }
            for sm in d.sub_mains
        ]

        boards = [d.building.db_anchor.id, *[sb.board_id for sb in d.building.sub_boards]]
        lumen_non_compliant = sum(1 for lc in d.lumen_checks if not lc.compliant)

        return {
            "scenario": name,
            "building_type": d.building.building_type.value,
            "total_area_m2": round(sum(r.area_m2 for r in d.building.rooms), 2),
            "room_count": len(d.building.rooms),
            "boards": boards,
            "supply_tier": eac.recommended_supply_tier,
            "main_breaker_a": eac.recommended_main_breaker_a,
            "diversified_kva": round(eac.total_diversified_kva, 2),
            "connected_kva": round(eac.total_connected_kva, 2),
            "imbalance_pct": round(pb.imbalance_pct, 2) if pb is not None else None,
            "total_conduit_m": round(sp.total_conduit_m, 2),
            "circuit_count": len(d.circuits),
            "all_circuits_pass": all(c.overall_pass for c in d.circuits),
            "device_count": len(sp.devices),
            "boq_subtotal_eur": round(boq.subtotal_eur, 2) if boq is not None else None,
            "boq_grand_total_eur": round(boq.grand_total_eur, 2) if boq is not None else None,
            "lps_required": bool(d.lightning_risk and d.lightning_risk.lps_required),
            "blocker_count": sum(1 for f in d.review_flags if f.severity.value == "blocker"),
            "warning_count": sum(1 for f in d.review_flags if f.severity.value == "warning"),
            "invariants": {
                "lumen_checks_count": len(d.lumen_checks),
                "lumen_non_compliant": lumen_non_compliant,
                "stacked_devices_lt_100mm": _count_stacked_devices_lt_100mm(p2),
                "dxf_audit_errors": dxf_audit_errors,
                "e201_subboard_overlaps": _count_e201_subboard_overlaps(sld_svg),
                "e301_scoped_css": 'class="cy-e301"' in e301_svg,
                "eac_html_has_literal_none": ">None<" in eac_html,
            },
            "sub_mains": sub_mains_sig,
            "circuits": circuits_sig,
        }


def compute_all_golden_snapshots() -> dict[str, dict[str, Any]]:
    return {
        name: compute_scenario_snapshot(name, tmpl, ov) for name, tmpl, ov in SCENARIOS
    }


def main() -> None:
    snapshots = compute_all_golden_snapshots()
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(snapshots, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(snapshots)} exact-match golden snapshots to {GOLDEN_PATH}")
    for name, snap in snapshots.items():
        print(
            f"  - {name}: area={snap['total_area_m2']}m², supply={snap['supply_tier']}, "
            f"div={snap['diversified_kva']}kVA, circuits={snap['circuit_count']}, "
            f"devices={snap['device_count']}, boq=€{snap['boq_grand_total_eur']}, "
            f"invariants={snap['invariants']}"
        )


if __name__ == "__main__":
    main()
