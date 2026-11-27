#!/usr/bin/env python3
"""Unified Live EU Cloud Run Operational E2E & Tier-3 Latency SLO Runner.

Modes:
  --env staging  Live Staging E2E (cold/warm Tier-3 SLO, OWASP headers, MIT .dwg
                 preview + 10-PDF ZIP + 0 DXF audit errors, MIT .ifc BIM, multi-floor)
  --env prod     Live Production SaaS E2E (Tier-3 SLO, /openapi.json==404, 401 auth gate,
                 register, password rotation, branding + 5% VAT, API-key rotation,
                 free-plan 1-seat limit, 3-rev carry-forward delta_kva > +5.0, branded
                 unpriced ZIP + 10 PDFs, cross-tenant 404 isolation, GDPR Art. 20 export,
                 and guaranteed finally-block GDPR Art. 17 erasure + post-erasure 401)
  --env monitor  Zero-write daily health, OWASP headers, auth-gate & latency monitor
"""

from __future__ import annotations

import argparse
import io
import json
import math
import secrets
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


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


def _check_owasp_headers(resp: httpx.Response, *, require_hsts: bool = True) -> None:
    hdrs = {k.lower(): v.lower() for k, v in resp.headers.items()}
    required = [
        "x-frame-options",
        "x-content-type-options",
        "referrer-policy",
        "content-security-policy",
    ]
    if require_hsts:
        required.append("strict-transport-security")
    for h in required:
        assert h in hdrs, f"Missing OWASP header '{h}' on {resp.url}"
    assert hdrs["x-frame-options"] == "deny", f"Bad X-Frame-Options: {hdrs['x-frame-options']}"
    assert hdrs["x-content-type-options"] == "nosniff", (
        f"Bad X-Content-Type-Options: {hdrs['x-content-type-options']}"
    )


def _check_health_with_cold_timing(
    client: httpx.Client,
    base_url: str,
    *,
    require_hsts: bool = True,
) -> tuple[dict[str, Any], float]:
    t0 = time.perf_counter()
    last_err = ""
    for attempt in range(6):
        try:
            resp = client.get(f"{base_url}/health", timeout=30.0)
            if resp.status_code == 200:
                _check_owasp_headers(resp, require_hsts=require_hsts)
                data = dict(resp.json())
                assert data["status"] == "ok", f"Unhealthy: {data}"
                assert data["store_backend"] == "firestore", f"Wrong backend: {data}"
                assert data.get("dwg_available") is True, f"dwg_available!=True: {data}"
                assert data.get("ifc_available") is True, f"ifc_available!=True: {data}"
                assert data.get("pdf_export_available") is True, f"pdf_export!=True: {data}"
                return data, time.perf_counter() - t0
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(min(2.0, 0.5 * (attempt + 1)))
    raise RuntimeError(f"Live /health failed after retries: {last_err}")


def _verify_zip_bundle(zip_bytes: bytes) -> dict[str, Any]:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = sorted(zf.namelist())
    pdfs = 0
    dxfs = 0
    audit_errors = 0
    with tempfile.TemporaryDirectory(prefix="cyelec_live_zip_") as tmp_dir:
        tmp = Path(tmp_dir)
        for name in names:
            raw = zf.read(name)
            if name.lower().endswith(".pdf"):
                assert raw.startswith(b"%PDF-"), f"Corrupt PDF header in {name}"
                assert len(raw) > 1024, f"Too small PDF {name}: {len(raw)}B"
                pdfs += 1
            elif name.lower().endswith(".dxf"):
                p = tmp / Path(name).name
                p.write_bytes(raw)
                doc = ezdxf.readfile(str(p))
                audit_errors += len(doc.audit().errors)
                dxfs += 1
    assert pdfs >= 11, f"Expected >= 11 WeasyPrint PDFs in live ZIP, got {pdfs}"
    assert "E203_selectivity_curves.pdf" in names, "Missing E203_selectivity_curves.pdf in live ZIP"
    assert dxfs >= 7, f"Expected >= 7 DXFs in live ZIP, got {dxfs}"
    assert audit_errors == 0, f"Expected 0 DXF audit errors in live ZIP, got {audit_errors}"
    return {
        "files": len(names),
        "zip_kb": round(len(zip_bytes) / 1024.0, 1),
        "pdfs": pdfs,
        "dxfs": dxfs,
        "dxf_audit_errors": audit_errors,
    }


def _run_staging(
    base_url: str,
    *,
    cold_ceiling_s: float,
    warm_p95_ceiling_s: float,
) -> dict[str, Any]:
    from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf

    report: dict[str, Any] = {"env": "staging", "url": base_url}
    dwg_bytes = MIT_DWG.read_bytes()
    ifc_bytes = MIT_IFC.read_bytes()

    with httpx.Client(timeout=120.0) as client:
        # 1. Cold-start /health + first solve (Tier-3 SLO < 25s)
        t_cold0 = time.perf_counter()
        health, health_s = _check_health_with_cold_timing(
            client, base_url, require_hsts=base_url.startswith("https://")
        )
        r_first = client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed", json={})
        assert r_first.status_code == 200, (
            f"First staging benchmark solve failed ({r_first.status_code}): {r_first.text[:300]}"
        )
        cold_total_s = time.perf_counter() - t_cold0
        assert cold_total_s < cold_ceiling_s, (
            f"Tier-3 Staging cold-start exceeded {cold_ceiling_s}s: {cold_total_s:.2f}s"
        )
        report["health"] = {
            "version": health["pipeline_version"],
            "health_s": round(health_s, 3),
            "cold_health_plus_first_solve_s": round(cold_total_s, 3),
        }

        # 2. Tier-3 Warm Solve p95 (3 warm calls < 5.0s)
        warm_times: list[float] = []
        for _ in range(3):
            tw = time.perf_counter()
            rw = client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed", json={})
            assert rw.status_code == 200
            warm_times.append(round(time.perf_counter() - tw, 3))
        warm_p95 = _percentile(warm_times, 95.0)
        assert warm_p95 < warm_p95_ceiling_s, (
            f"Tier-3 Staging warm p95 ({warm_p95:.2f}s) exceeded {warm_p95_ceiling_s}s"
        )
        report["warm_solve_slo"] = {"samples_s": warm_times, "p95_s": round(warm_p95, 3)}

        # 3. MIT Binary .dwg preview + 10-PDF ZIP
        r_dwg = client.post(
            f"{base_url}/v1/solve/dxf/preview",
            files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
        )
        assert r_dwg.status_code == 200, f"Staging .dwg preview failed: {r_dwg.text[:300]}"
        dwg_d = r_dwg.json()["result"]["design"]
        assert len(dwg_d["building"]["rooms"]) >= 8

        t_zip = time.perf_counter()
        r_zip = client.post(
            f"{base_url}/v1/solve/dxf/bundle",
            files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
        )
        zip_s = time.perf_counter() - t_zip
        assert r_zip.status_code == 200, f"Staging .dwg bundle failed: {r_zip.text[:300]}"
        zip_stats = _verify_zip_bundle(r_zip.content)
        zip_stats["elapsed_s"] = round(zip_s, 3)
        report["dwg_bundle"] = zip_stats

        # 4. MIT IFC4 BIM preview
        r_ifc = client.post(
            f"{base_url}/v1/solve/dxf/preview",
            files={"file": ("cyprus_minimal_house.ifc", ifc_bytes, "application/x-step")},
        )
        assert r_ifc.status_code == 200, f"Staging .ifc preview failed: {r_ifc.text[:300]}"
        ifc_rooms = r_ifc.json()["result"]["design"]["building"]["rooms"]
        assert len(ifc_rooms) == 6
        report["ifc_preview"] = {
            "rooms": len(ifc_rooms),
            "area_m2": sum(r["area_m2"] for r in ifc_rooms),
        }

        # 5. Live Multi-Floor DXF Upload
        with tempfile.TemporaryDirectory(prefix="cyelec_mf_live_") as tmp_dir:
            tmp = Path(tmp_dir)
            gf_path = tmp / "gf.dxf"
            f1_path = tmp / "1f.dxf"
            generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=gf_path)
            generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=f1_path)
            r_mf = client.post(
                f"{base_url}/v1/solve/dxf/multifloor",
                files=[
                    ("files", ("gf.dxf", gf_path.read_bytes(), "application/dxf")),
                    ("files", ("1f.dxf", f1_path.read_bytes(), "application/dxf")),
                ],
                data={"floor_labels_json": json.dumps(["GF", "1F"])},
            )
            assert r_mf.status_code == 200, (
                f"Staging multifloor failed ({r_mf.status_code}): {r_mf.text[:300]}"
            )
            mf_design = r_mf.json()["result"]["design"]
            assert len(mf_design.get("sub_mains", [])) >= 2
            report["multifloor"] = {
                "rooms": len(mf_design["building"]["rooms"]),
                "sub_mains": len(mf_design["sub_mains"]),
                "circuits": len(mf_design["circuits"]),
            }

    return report


def _run_prod(
    base_url: str,
    *,
    cold_ceiling_s: float,
    warm_p95_ceiling_s: float,
    invite_code: str = "",
) -> dict[str, Any]:
    report: dict[str, Any] = {"env": "prod", "url": base_url}
    dwg_bytes = MIT_DWG.read_bytes()
    run_id = f"{int(time.time())}-{secrets.token_hex(3)}"
    email_a = f"qa-ops-{run_id}-a@cyelec-test.invalid"
    email_b = f"qa-ops-{run_id}-b@cyelec-test.invalid"
    pw1 = f"CyElecQa!{secrets.token_hex(6)}"
    pw2 = f"CyElecRot!{secrets.token_hex(6)}"
    token_a: str | None = None
    token_b: str | None = None
    api_key_a2: str | None = None

    with httpx.Client(timeout=120.0) as client:
        try:
            # 1. Cold health + OWASP headers + prod surface hardening
            t_cold0 = time.perf_counter()
            health, health_s = _check_health_with_cold_timing(
                client, base_url, require_hsts=base_url.startswith("https://")
            )
            r_oas = client.get(f"{base_url}/openapi.json")
            assert r_oas.status_code == 404, (
                f"Expected prod /openapi.json 404, got {r_oas.status_code}"
            )
            for page in ("/", "/guide"):
                r_pg = client.get(f"{base_url}{page}")
                assert r_pg.status_code == 200
                assert 'href="/docs"' not in r_pg.text, f"Prod {page} exposed /docs link!"
            r_unauth = client.get(f"{base_url}/v1/projects")
            assert r_unauth.status_code == 401, (
                f"Expected unauthenticated /v1/projects 401, got {r_unauth.status_code}"
            )

            # 2. Register Tenant A
            reg_payload_a: dict[str, Any] = {
                "firm_name": f"CyElec QA Firm {run_id}",
                "email": email_a,
                "password": pw1,
                "display_name": "CyElec QA Lead",
            }
            if invite_code:
                reg_payload_a["invite_code"] = invite_code
            r_reg_a = client.post(f"{base_url}/v1/auth/register", json=reg_payload_a)
            assert r_reg_a.status_code == 201, f"Register A failed: {r_reg_a.text[:300]}"
            token_a = r_reg_a.json()["access_token"]
            hdr_a = {"Authorization": f"Bearer {token_a}"}

            # First solve under cold window
            r_first = client.post(
                f"{base_url}/v1/solve/benchmark/apartment_3bed", json={}, headers=hdr_a
            )
            assert r_first.status_code == 200, f"Prod first solve failed: {r_first.text[:300]}"
            cold_total_s = time.perf_counter() - t_cold0
            assert cold_total_s < cold_ceiling_s, (
                f"Tier-3 Prod cold-start exceeded {cold_ceiling_s}s: {cold_total_s:.2f}s"
            )
            report["health"] = {
                "version": health["pipeline_version"],
                "health_s": round(health_s, 3),
                "cold_health_plus_first_solve_s": round(cold_total_s, 3),
                "openapi_status": r_oas.status_code,
                "unauth_projects_status": r_unauth.status_code,
            }

            # 3. Tier-3 Warm Latency SLO (3 warm calls < 5.0s p95)
            warm_times: list[float] = []
            for _ in range(3):
                tw = time.perf_counter()
                rw = client.post(
                    f"{base_url}/v1/solve/benchmark/apartment_3bed", json={}, headers=hdr_a
                )
                assert rw.status_code == 200
                warm_times.append(round(time.perf_counter() - tw, 3))
            warm_p95 = _percentile(warm_times, 95.0)
            assert warm_p95 < warm_p95_ceiling_s, (
                f"Tier-3 Prod warm p95 ({warm_p95:.2f}s) exceeded {warm_p95_ceiling_s}s"
            )
            report["warm_solve_slo"] = {"samples_s": warm_times, "p95_s": round(warm_p95, 3)}

            # 4. Password rotation + old-password 401
            r_chpw = client.post(
                f"{base_url}/v1/auth/change-password",
                json={"current_password": pw1, "new_password": pw2},
                headers=hdr_a,
            )
            assert r_chpw.status_code == 200, f"Password change failed: {r_chpw.text[:300]}"
            r_old_login = client.post(
                f"{base_url}/v1/auth/login", json={"email": email_a, "password": pw1}
            )
            assert r_old_login.status_code == 401, "Old password still accepted after rotation!"
            r_new_login = client.post(
                f"{base_url}/v1/auth/login", json={"email": email_a, "password": pw2}
            )
            assert r_new_login.status_code == 200, (
                f"New password login failed: {r_new_login.text[:300]}"
            )
            token_a = r_new_login.json()["access_token"]
            hdr_a = {"Authorization": f"Bearer {token_a}"}

            # 5. Branding & 5% Cyprus primary-residence VAT + API key rotation
            r_brand = client.put(
                f"{base_url}/v1/tenants/me",
                json={
                    "branding": {
                        "firm_name": f"QA Consultants Ltd {run_id}",
                        "engineer_name": "E. Papadopoulos (ETEK QA)",
                        "etek_reg_no": "ETEK-QA-9999",
                    },
                    "boq_rate_overrides": {"vat_rate": 0.05},
                },
                headers=hdr_a,
            )
            assert r_brand.status_code == 200, f"Branding update failed: {r_brand.text[:300]}"
            r_k1 = client.post(f"{base_url}/v1/tenants/me/api-key", headers=hdr_a)
            assert r_k1.status_code == 200
            api_key_a1 = r_k1.json()["api_key"]
            r_k2 = client.post(f"{base_url}/v1/tenants/me/api-key", headers=hdr_a)
            assert r_k2.status_code == 200
            api_key_a2 = r_k2.json()["api_key"]
            assert (
                client.get(f"{base_url}/v1/projects", headers={"X-API-Key": api_key_a1}).status_code
                == 401
            )
            assert (
                client.get(f"{base_url}/v1/projects", headers={"X-API-Key": api_key_a2}).status_code
                == 200
            )

            # 6. Free-plan 1-seat quota enforcement
            r_seat = client.post(
                f"{base_url}/v1/team/members",
                json={
                    "email": f"qa-extra-{run_id}@cyelec-test.invalid",
                    "display_name": "Extra Engineer",
                    "role": "engineer",
                    "password": pw2,
                },
                headers=hdr_a,
            )
            assert r_seat.status_code in (400, 402, 403) and "seat limit" in r_seat.text.lower(), (
                f"Expected seat limit rejection, got {r_seat.status_code}: {r_seat.text[:200]}"
            )

            # 7. 3-Revision Project Carry-Forward Workflow + Diff (diversified_kva_delta > +5.0)
            r_proj = client.post(
                f"{base_url}/v1/projects",
                json={"name": f"Live QA Villa {run_id}", "description": "QA-CY-001"},
                headers=hdr_a,
            )
            assert r_proj.status_code == 201
            pid_a = r_proj.json()["project_id"]

            r_rev1 = client.post(
                f"{base_url}/v1/projects/{pid_a}/revisions/dxf",
                files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
                data={"notes": "Rev 1 baseline"},
                headers=hdr_a,
            )
            assert r_rev1.status_code == 201, f"Rev 1 failed: {r_rev1.text[:300]}"

            rev2_ov = {
                "pv_capacity_kva": 6.0,
                "residential_brief": {
                    "ev_charger_kw": 11.0,
                    "include_pool": True,
                },
            }
            r_rev2 = client.post(
                f"{base_url}/v1/projects/{pid_a}/revisions/dxf",
                files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
                data={
                    "overrides_json": json.dumps(rev2_ov),
                    "carry_forward": "true",
                    "notes": "Rev 2 PV+EV+Pool",
                },
                headers=hdr_a,
            )
            assert r_rev2.status_code == 201, f"Rev 2 failed: {r_rev2.text[:300]}"

            r_rev3 = client.post(
                f"{base_url}/v1/projects/{pid_a}/revisions/dxf",
                files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
                data={"carry_forward": "true", "notes": "Rev 3 carry-forward check"},
                headers=hdr_a,
            )
            assert r_rev3.status_code == 201, f"Rev 3 failed: {r_rev3.text[:300]}"
            rev3_ov = r_rev3.json().get("overrides") or {}
            assert rev3_ov.get("pv_capacity_kva") == 6.0, (
                f"PV override lost on carry-forward: {rev3_ov}"
            )
            rb3 = rev3_ov.get("residential_brief") or {}
            assert rb3.get("ev_charger_kw") == 11.0, f"EV override lost on carry-forward: {rb3}"
            assert rb3.get("include_pool") is True, f"Pool override lost on carry-forward: {rb3}"

            r_diff = client.get(f"{base_url}/v1/projects/{pid_a}/diff/1/3", headers=hdr_a)
            assert r_diff.status_code == 200
            delta_kva = float(r_diff.json()["diversified_kva_delta"])
            assert delta_kva > 5.0, (
                f"Expected Rev1->Rev3 diversified_kva_delta > +5.0, got {delta_kva}"
            )

            # 8. Branded Unpriced Tender ZIP + >= 10 WeasyPrint PDFs + 0 DXF audit errors
            t_zip = time.perf_counter()
            r_zip = client.post(
                f"{base_url}/v1/solve/dxf/bundle",
                files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
                data={"overrides_json": json.dumps({"tender_boq_blank_rates": True})},
                headers=hdr_a,
            )
            zip_s = time.perf_counter() - t_zip
            assert r_zip.status_code == 200, f"Prod branded ZIP failed: {r_zip.text[:300]}"
            zip_stats = _verify_zip_bundle(r_zip.content)
            zip_stats["elapsed_s"] = round(zip_s, 3)

            # 9. Cross-Tenant Isolation (Tenant B MUST get 404 on Tenant A project)
            reg_payload_b: dict[str, Any] = {
                "firm_name": f"Tenant B Firm {run_id}",
                "email": email_b,
                "password": pw1,
                "display_name": "Tenant B Auditor",
            }
            if invite_code:
                reg_payload_b["invite_code"] = invite_code
            r_reg_b = client.post(f"{base_url}/v1/auth/register", json=reg_payload_b)
            assert r_reg_b.status_code == 201
            token_b = r_reg_b.json()["access_token"]
            hdr_b = {"Authorization": f"Bearer {token_b}"}
            assert client.get(f"{base_url}/v1/projects/{pid_a}", headers=hdr_b).status_code == 404
            assert (
                client.get(f"{base_url}/v1/projects/{pid_a}/revisions", headers=hdr_b).status_code
                == 404
            )

            # 10. GDPR Art. 20 Data Export
            r_exp = client.get(f"{base_url}/v1/tenants/me/export", headers=hdr_a)
            assert r_exp.status_code == 200
            exp_data = r_exp.json()
            assert len(exp_data.get("projects", [])) == 1
            revs_a = exp_data.get("revisions_by_project", {}).get(pid_a, [])
            assert len(revs_a) == 3, f"Expected 3 exported revisions, got {len(revs_a)}"

            report["saas_lifecycle"] = {
                "password_rotation": "pass",
                "api_key_rotation": "pass",
                "free_seat_enforcement_status": r_seat.status_code,
                "carry_forward_delta_kva": round(delta_kva, 2),
                "branded_zip": zip_stats,
                "cross_tenant_isolation": "404_verified",
                "gdpr_art20_revisions": len(revs_a),
            }
        finally:
            # Guaranteed GDPR Art. 17 erasure of EVERY ephemeral QA tenant
            if token_a:
                r_del_a = client.delete(
                    f"{base_url}/v1/tenants/me", headers={"Authorization": f"Bearer {token_a}"}
                )
                assert r_del_a.status_code == 200, (
                    f"GDPR Art. 17 delete A failed: {r_del_a.text[:200]}"
                )
                # Post-erasure JWT & API key MUST return 401
                assert (
                    client.get(
                        f"{base_url}/v1/auth/me", headers={"Authorization": f"Bearer {token_a}"}
                    ).status_code
                    == 401
                )
                if api_key_a2:
                    assert (
                        client.get(
                            f"{base_url}/v1/projects", headers={"X-API-Key": api_key_a2}
                        ).status_code
                        == 401
                    )
                report["gdpr_art17_cleanup_a"] = "erased_and_401_verified"
            if token_b:
                r_del_b = client.delete(
                    f"{base_url}/v1/tenants/me", headers={"Authorization": f"Bearer {token_b}"}
                )
                assert r_del_b.status_code == 200, (
                    f"GDPR Art. 17 delete B failed: {r_del_b.text[:200]}"
                )
                report["gdpr_art17_cleanup_b"] = "erased"

    return report


def _run_monitor(base_url: str, *, is_prod: bool, cold_ceiling_s: float) -> dict[str, Any]:
    """Zero-write daily synthetic probe (writes zero Firestore docs, costs €0.00)."""
    with httpx.Client(timeout=45.0) as client:
        health, health_s = _check_health_with_cold_timing(
            client, base_url, require_hsts=base_url.startswith("https://")
        )
        assert health_s < cold_ceiling_s, (
            f"Monitor /health exceeded {cold_ceiling_s}s: {health_s:.2f}s"
        )
        res: dict[str, Any] = {
            "env": "monitor_prod" if is_prod else "monitor_staging",
            "url": base_url,
            "version": health["pipeline_version"],
            "health_s": round(health_s, 3),
        }
        if is_prod:
            r_oas = client.get(f"{base_url}/openapi.json")
            assert r_oas.status_code == 404, (
                f"Expected prod /openapi.json 404, got {r_oas.status_code}"
            )
            r_unauth = client.get(f"{base_url}/v1/projects")
            assert r_unauth.status_code == 401, (
                f"Expected prod /v1/projects 401, got {r_unauth.status_code}"
            )
            res["openapi_status"] = r_oas.status_code
            res["unauth_projects_status"] = r_unauth.status_code
        else:
            t_w = time.perf_counter()
            rw = client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed", json={})
            assert rw.status_code == 200
            res["staging_solve_s"] = round(time.perf_counter() - t_w, 3)
        return res


def main() -> int:
    import os

    parser = argparse.ArgumentParser(
        description="CyElec Live EU Cloud Run E2E & Tier-3 Perf Runner"
    )
    parser.add_argument("--env", choices=["staging", "prod", "monitor"], required=True)
    parser.add_argument("--url", required=True, help="Live Cloud Run base URL")
    parser.add_argument(
        "--is-prod", action="store_true", help="Used with --env monitor for prod checks"
    )
    parser.add_argument(
        "--invite-code",
        default=os.environ.get("CYELEC_PILOT_INVITE_CODE", ""),
        help="Optional pilot invite code for /v1/auth/register on Production",
    )
    parser.add_argument("--cold-ceiling-s", type=float, default=25.0)
    parser.add_argument("--warm-p95-ceiling-s", type=float, default=5.0)
    parser.add_argument("--report", default="", help="Optional JSON telemetry output path")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    if args.env == "staging":
        report = _run_staging(
            base_url,
            cold_ceiling_s=args.cold_ceiling_s,
            warm_p95_ceiling_s=args.warm_p95_ceiling_s,
        )
    elif args.env == "prod":
        report = _run_prod(
            base_url,
            cold_ceiling_s=args.cold_ceiling_s,
            warm_p95_ceiling_s=args.warm_p95_ceiling_s,
            invite_code=args.invite_code,
        )
    else:
        report = _run_monitor(
            base_url,
            is_prod=args.is_prod,
            cold_ceiling_s=args.cold_ceiling_s,
        )

    out = json.dumps(report, indent=2)
    print(out)
    if args.report:
        Path(args.report).write_text(out, encoding="utf-8")
    print(f"PASS: Live Operational E2E & Tier-3 Perf ({args.env})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
