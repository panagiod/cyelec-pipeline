#!/usr/bin/env python3
"""Container Integration E2E Smoke Test for Production Dockerfile.

Validates inside a running container (or live staging URL) that all optional native
production dependencies work end-to-end with 100% MIT-original CAD fixtures:
  1. LibreDWG (`dwg2dxf`) binary AutoCAD `.dwg` ingestion -> preview + full ZIP pack
  2. IfcOpenShell (`ifcopenshell`) IFC4 BIM `.ifc` ingestion -> preview
  3. WeasyPrint native PDF export -> >= 10 real `%PDF-` documents inside the ZIP
  4. `ezdxf` audit -> 0 DXF audit errors across all exported CAD drawings
  5. OWASP security headers on `/health`
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import ezdxf
import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
CAD_DIR = REPO_ROOT / "tests" / "fixtures" / "cad"
MIT_DWG = CAD_DIR / "cyprus_minimal_house.dwg"
MIT_IFC = CAD_DIR / "cyprus_minimal_house.ifc"


def wait_for_health(client: httpx.Client, url: str, timeout_s: float = 45.0) -> dict[str, Any]:
    """Poll /health until container is ready or timeout expires."""
    deadline = time.perf_counter() + timeout_s
    last_err = ""
    while time.perf_counter() < deadline:
        try:
            resp = client.get(f"{url}/health", timeout=10.0)
            if resp.status_code == 200:
                hdrs = {k.lower(): v.lower() for k, v in resp.headers.items()}
                for req_hdr in (
                    "x-frame-options",
                    "x-content-type-options",
                    "referrer-policy",
                    "content-security-policy",
                ):
                    if req_hdr not in hdrs:
                        raise AssertionError(f"Missing OWASP header '{req_hdr}' on /health")
                if hdrs.get("x-frame-options") != "deny":
                    raise AssertionError(f"Unexpected X-Frame-Options: {hdrs['x-frame-options']}")
                if hdrs.get("x-content-type-options") != "nosniff":
                    raise AssertionError(
                        f"Unexpected X-Content-Type-Options: {hdrs['x-content-type-options']}"
                    )
                return dict(resp.json())
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Service at {url}/health did not become ready in {timeout_s}s: {last_err}")


def verify_zip_pack(zip_bytes: bytes) -> dict[str, Any]:
    """Validate every PDF magic header and ezdxf audit error count in an EAC ZIP bundle."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = sorted(zf.namelist())
    pdfs: list[str] = []
    dxfs: list[str] = []
    dxf_audit_errors: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix="cyelec_zip_audit_") as tmp_dir:
        tmp = Path(tmp_dir)
        for name in names:
            raw = zf.read(name)
            if name.lower().endswith(".pdf"):
                if not raw.startswith(b"%PDF-"):
                    raise AssertionError(f"Invalid PDF header in {name}: {raw[:16]!r}")
                if len(raw) < 1024:
                    raise AssertionError(f"Suspiciously tiny PDF {name}: {len(raw)} bytes")
                pdfs.append(name)
            elif name.lower().endswith(".dxf"):
                dxf_path = tmp / Path(name).name
                dxf_path.write_bytes(raw)
                doc = ezdxf.readfile(str(dxf_path))
                errs = len(doc.audit().errors)
                dxf_audit_errors[name] = errs
                dxfs.append(name)

    bad_dxfs = {k: v for k, v in dxf_audit_errors.items() if v > 0}
    if bad_dxfs:
        raise AssertionError(f"Non-zero DXF audit errors in ZIP bundle: {bad_dxfs}")
    if len(pdfs) < 11:
        raise AssertionError(f"Expected >= 11 WeasyPrint PDFs in ZIP, found {len(pdfs)}: {pdfs}")
    if len(dxfs) < 7:
        raise AssertionError(f"Expected >= 7 DXF drawings in ZIP, found {len(dxfs)}: {dxfs}")

    for required_name in (
        "eac_application_form.pdf",
        "calc_report.pdf",
        "eac_submission_pack.pdf",
        "E101_lighting_layout.pdf",
        "E102_power_layout.pdf",
        "E103_elv_layout.pdf",
        "E104_site_earthing_plan.pdf",
        "E201_single_line_diagram.pdf",
        "E202_riser_and_db_layout.pdf",
        "E203_selectivity_curves.pdf",
        "E301_details_notes.pdf",
        "tender_boq_10section.xlsx",
        "manifest.json",
    ):
        if required_name not in names:
            raise AssertionError(f"Required deliverable missing from ZIP: {required_name}")

    return {
        "total_files": len(names),
        "zip_kb": round(len(zip_bytes) / 1024.0, 1),
        "pdf_count": len(pdfs),
        "dxf_count": len(dxfs),
        "dxf_audit_errors_total": sum(dxf_audit_errors.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="CyElec Docker Container E2E Smoke Test")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Base URL of CyElec API")
    parser.add_argument(
        "--bundle-ceiling-s",
        type=float,
        default=60.0,
        help="Hard latency ceiling (seconds) for single .dwg + 10-PDF ZIP pack",
    )
    parser.add_argument("--report", default="", help="Optional path to write JSON report")
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    if not MIT_DWG.is_file():
        raise FileNotFoundError(f"Missing MIT .dwg fixture: {MIT_DWG}")
    if not MIT_IFC.is_file():
        raise FileNotFoundError(f"Missing MIT .ifc fixture: {MIT_IFC}")

    report: dict[str, Any] = {"url": base_url}

    with httpx.Client(timeout=120.0) as client:
        # 1. Health & optional native engines check
        health = wait_for_health(client, base_url)
        assert health.get("status") == "ok", f"Unhealthy response: {health}"
        assert health.get("dwg_available") is True, f"LibreDWG dwg2dxf missing: {health}"
        assert health.get("ifc_available") is True, f"IfcOpenShell missing: {health}"
        assert health.get("pdf_export_available") is True, f"WeasyPrint missing: {health}"
        report["health"] = {
            "pipeline_version": health.get("pipeline_version"),
            "store_backend": health.get("store_backend"),
            "dwg_available": health.get("dwg_available"),
            "ifc_available": health.get("ifc_available"),
            "pdf_export_available": health.get("pdf_export_available"),
        }

        # 2. Binary .dwg preview (LibreDWG subprocess)
        t0 = time.perf_counter()
        dwg_bytes = MIT_DWG.read_bytes()
        r_dwg_prev = client.post(
            f"{base_url}/v1/solve/dxf/preview",
            files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
        )
        assert r_dwg_prev.status_code == 200, (
            f".dwg preview failed ({r_dwg_prev.status_code}): {r_dwg_prev.text[:400]}"
        )
        dwg_prev = r_dwg_prev.json()
        dwg_prev_s = time.perf_counter() - t0
        dwg_rooms = dwg_prev["result"]["design"]["building"]["rooms"]
        dwg_circuits = dwg_prev["result"]["design"]["circuits"]
        assert len(dwg_rooms) >= 8, f"Expected >= 8 rooms from MIT .dwg, got {len(dwg_rooms)}"
        assert len(dwg_circuits) >= 10, (
            f"Expected >= 10 circuits from MIT .dwg, got {len(dwg_circuits)}"
        )
        for svg_key in ("lighting_svg", "power_svg", "elv_svg", "sld_svg", "details_svg"):
            assert "<svg" in dwg_prev.get(svg_key, ""), f"Missing {svg_key} in preview response"
        report["dwg_preview"] = {
            "elapsed_s": round(dwg_prev_s, 3),
            "rooms": len(dwg_rooms),
            "circuits": len(dwg_circuits),
            "supply": dwg_prev["result"]["design"]["eac_compliance"]["recommended_supply_tier"],
        }

        # 3. Binary .dwg full ZIP pack with >= 10 WeasyPrint PDFs & 0 DXF audit errors
        t1 = time.perf_counter()
        r_dwg_zip = client.post(
            f"{base_url}/v1/solve/dxf/bundle",
            files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
        )
        dwg_zip_s = time.perf_counter() - t1
        assert r_dwg_zip.status_code == 200, (
            f".dwg bundle failed ({r_dwg_zip.status_code}): {r_dwg_zip.text[:400]}"
        )
        zip_stats = verify_zip_pack(r_dwg_zip.content)
        zip_stats["elapsed_s"] = round(dwg_zip_s, 3)
        report["dwg_bundle"] = zip_stats
        assert dwg_zip_s < args.bundle_ceiling_s, (
            f"Single .dwg + 10-PDF ZIP exceeded {args.bundle_ceiling_s}s ceiling: {dwg_zip_s:.2f}s"
        )

        # 4. IFC4 BIM preview (IfcOpenShell)
        t2 = time.perf_counter()
        ifc_bytes = MIT_IFC.read_bytes()
        r_ifc = client.post(
            f"{base_url}/v1/solve/dxf/preview",
            files={"file": ("cyprus_minimal_house.ifc", ifc_bytes, "application/x-step")},
        )
        ifc_s = time.perf_counter() - t2
        assert r_ifc.status_code == 200, (
            f".ifc preview failed ({r_ifc.status_code}): {r_ifc.text[:400]}"
        )
        ifc_data = r_ifc.json()
        ifc_rooms = ifc_data["result"]["design"]["building"]["rooms"]
        ifc_area = sum(r["area_m2"] for r in ifc_rooms)
        assert len(ifc_rooms) == 6, f"Expected 6 IfcSpace rooms in MIT .ifc, got {len(ifc_rooms)}"
        assert abs(ifc_area - 95.0) < 0.2, f"Expected 95.0 m² in MIT .ifc, got {ifc_area}"
        report["ifc_preview"] = {
            "elapsed_s": round(ifc_s, 3),
            "rooms": len(ifc_rooms),
            "total_area_m2": round(ifc_area, 2),
            "circuits": len(ifc_data["result"]["design"]["circuits"]),
        }

    out = json.dumps(report, indent=2)
    print(out)
    if args.report:
        Path(args.report).write_text(out, encoding="utf-8")
    print(
        "PASS: Docker Container E2E Smoke (.dwg + .ifc + 10 WeasyPrint PDFs + 0 DXF audit errors)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
