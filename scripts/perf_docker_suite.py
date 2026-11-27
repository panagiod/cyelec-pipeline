#!/usr/bin/env python3
"""Tier-2 Docker (2 vCPU / 2 GiB) Concurrency & Warm Memory-Leak Soak Harness.

Matches the exact EU Cloud Run production container profile (`--cpus=2 --memory=2g
--memory-swap=2g`, zero swap) and enforces hard safety ceilings:
  - 4x Parallel Concurrent ZIP Bundle Downloads:
      * 0 OOM kills, 0 HTTP 5xx / errors
      * p95 latency < 90.0 s
      * Peak container RSS < 1,500 MiB
  - 30-Solve Warm Memory-Leak Soak:
      * 30/30 HTTP 200
      * RSS growth (end - warm soak baseline) < 150 MiB
      * Peak RSS < 1,500 MiB

Supports both:
  1. Container lifecycle mode (`--image cyelec-ci:test`) — used in CI & local Docker
  2. External server mode (`--url http://127.0.0.1:8080 --pid <PID>`) — local profiling
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import math
import re
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
CAD_DIR = REPO_ROOT / "tests" / "fixtures" / "cad"
MIT_DWG = CAD_DIR / "cyprus_minimal_house.dwg"
MIT_IFC = CAD_DIR / "cyprus_minimal_house.ifc"

_MEM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?i?B)", re.IGNORECASE)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _parse_mem_mib(raw: str) -> float:
    """Parse docker stats memory token like '412.5MiB / 2GiB' into MiB."""
    first = raw.split("/")[0].strip()
    m = _MEM_RE.search(first)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("b",):
        return val / (1024 * 1024)
    if unit in ("kb", "kib"):
        return val / 1024.0
    if unit in ("mb", "mib"):
        return val
    if unit in ("gb", "gib"):
        return val * 1024.0
    return val


def _read_rss_mib(*, container_name: str | None, pid: int | None) -> float:
    if container_name:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return _parse_mem_mib(proc.stdout.strip())
        return 0.0
    if pid is not None:
        status_file = Path(f"/proc/{pid}/status")
        if status_file.is_file():
            for line in status_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) / 1024.0
    return 0.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


def _wait_health(base_url: str, timeout_s: float = 45.0) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_s
    last_err = ""
    with httpx.Client(timeout=10.0) as client:
        while time.perf_counter() < deadline:
            try:
                resp = client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    return dict(resp.json())
                last_err = f"HTTP {resp.status_code}"
            except Exception as exc:
                last_err = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"Server at {base_url}/health failed to start: {last_err}")


def _run_one_bundle(
    base_url: str,
    *,
    use_dwg: bool,
    dwg_bytes: bytes,
    expect_pdf: bool,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    with httpx.Client(timeout=180.0) as client:
        if use_dwg:
            resp = client.post(
                f"{base_url}/v1/solve/dxf/bundle",
                files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
            )
        else:
            resp = client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed/bundle", json={})
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        return {
            "status": resp.status_code,
            "elapsed_s": round(elapsed, 3),
            "error": resp.text[:300],
        }
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    pdfs = [n for n in names if n.lower().endswith(".pdf")]
    if expect_pdf and len(pdfs) < 10:
        return {
            "status": 500,
            "elapsed_s": round(elapsed, 3),
            "error": f"Expected >=10 PDFs in ZIP, got {len(pdfs)}",
        }
    return {
        "status": 200,
        "elapsed_s": round(elapsed, 3),
        "zip_kb": round(len(resp.content) / 1024.0, 1),
        "files": len(names),
        "pdfs": len(pdfs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="CyElec Tier-2 Concurrency & Soak Perf Harness")
    parser.add_argument(
        "--image", default="", help="Docker image to launch with --cpus=2 --memory=2g"
    )
    parser.add_argument("--url", default="", help="Existing server URL (skip docker run)")
    parser.add_argument("--pid", type=int, default=None, help="Optional server PID for /proc RSS")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel ZIP bundle workers")
    parser.add_argument("--soak-iters", type=int, default=30, help="Warm soak iterations")
    parser.add_argument("--p95-ceiling-s", type=float, default=90.0)
    parser.add_argument("--peak-rss-ceiling-mib", type=float, default=1500.0)
    parser.add_argument("--leak-ceiling-mib", type=float, default=150.0)
    parser.add_argument("--report", default="", help="Optional JSON telemetry output path")
    args = parser.parse_args()

    if not args.image and not args.url:
        raise SystemExit("Provide either --image <docker_image> or --url <http://...>")

    container_name: str | None = None
    base_url = args.url.rstrip("/")

    if args.image:
        port = _find_free_port()
        container_name = f"cyelec-perf-tier2-{int(time.time())}"
        base_url = f"http://127.0.0.1:{port}"
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "--cpus=2",
            "--memory=2g",
            "--memory-swap=2g",
            "-p",
            f"127.0.0.1:{port}:8080",
            "-e",
            "CYELEC_ENV=staging",
            "-e",
            "CYELEC_AUTH_REQUIRED=false",
            "-e",
            "CYELEC_ALLOW_ANONYMOUS=true",
            "-e",
            "CYELEC_MAX_CONCURRENT_BUNDLES=2",
            args.image,
        ]
        subprocess.run(cmd, check=True)

    rss_samples: list[float] = []
    stop_evt = threading.Event()

    def _sample_rss() -> None:
        while not stop_evt.is_set():
            val = _read_rss_mib(container_name=container_name, pid=args.pid)
            if val > 0:
                rss_samples.append(val)
            stop_evt.wait(0.35)

    sampler = threading.Thread(target=_sample_rss, daemon=True)

    try:
        health = _wait_health(base_url, timeout_s=60.0)
        use_dwg = bool(health.get("dwg_available")) and MIT_DWG.is_file()
        use_ifc = bool(health.get("ifc_available")) and MIT_IFC.is_file()
        expect_pdf = bool(health.get("pdf_export_available"))
        dwg_bytes = MIT_DWG.read_bytes() if MIT_DWG.is_file() else b""
        ifc_bytes = MIT_IFC.read_bytes() if MIT_IFC.is_file() else b""

        # Warmup 3 solves so JIT/caches/fonts/arenas settle before measuring baseline
        with httpx.Client(timeout=120.0) as client:
            for _ in range(3):
                r = client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed", json={})
                assert r.status_code == 200, f"Warmup failed: {r.status_code}"
        rss_after_warmup = _read_rss_mib(container_name=container_name, pid=args.pid)

        sampler.start()

        # Workload A: 4x Parallel Concurrent ZIP Bundle Downloads
        t_conc0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futs = [
                pool.submit(
                    _run_one_bundle,
                    base_url,
                    use_dwg=use_dwg,
                    dwg_bytes=dwg_bytes,
                    expect_pdf=expect_pdf,
                )
                for _ in range(args.concurrency)
            ]
            bundle_results = [f.result() for f in futs]
        conc_wall_s = time.perf_counter() - t_conc0
        conc_latencies = [r["elapsed_s"] for r in bundle_results]
        conc_p95_s = _percentile(conc_latencies, 95.0)
        conc_errors = [r for r in bundle_results if r["status"] != 200]
        peak_rss_conc = max(rss_samples) if rss_samples else rss_after_warmup

        # Workload B: 30-Solve Warm Memory-Leak Soak
        soak_latencies: list[float] = []
        soak_rss_series: list[float] = []
        soak_errors = 0

        with httpx.Client(timeout=60.0) as client:
            # Settle once after heavy PDF bundle so soak baseline reflects steady warm RSS
            client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed", json={})
            rss_soak_start = _read_rss_mib(container_name=container_name, pid=args.pid)

            for i in range(args.soak_iters):
                t_i = time.perf_counter()
                mode = i % 3
                if mode == 0:
                    resp = client.post(f"{base_url}/v1/solve/benchmark/apartment_3bed", json={})
                elif mode == 1 and use_dwg:
                    resp = client.post(
                        f"{base_url}/v1/solve/dxf/preview",
                        files={"file": ("cyprus_minimal_house.dwg", dwg_bytes, "application/acad")},
                    )
                elif mode == 2 and use_ifc:
                    resp = client.post(
                        f"{base_url}/v1/solve/dxf/preview",
                        files={
                            "file": ("cyprus_minimal_house.ifc", ifc_bytes, "application/x-step")
                        },
                    )
                else:
                    resp = client.post(f"{base_url}/v1/solve/benchmark/villa_2storey", json={})
                soak_latencies.append(round(time.perf_counter() - t_i, 3))
                if resp.status_code != 200:
                    soak_errors += 1
                cur_rss = _read_rss_mib(container_name=container_name, pid=args.pid)
                if cur_rss > 0:
                    soak_rss_series.append(round(cur_rss, 1))

        stop_evt.set()
        sampler.join(timeout=3.0)

        rss_soak_end = soak_rss_series[-1] if soak_rss_series else rss_soak_start
        rss_growth_mib = round(max(0.0, rss_soak_end - rss_soak_start), 2)
        overall_peak_rss = round(max(rss_samples or [rss_soak_end]), 2)

        oom_killed = False
        if container_name:
            insp = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.OOMKilled}}", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            oom_killed = insp.stdout.strip().lower() == "true"

        report: dict[str, Any] = {
            "mode": "docker_2vcpu_2gib" if container_name else "external_url",
            "health": {
                "dwg_available": health.get("dwg_available"),
                "ifc_available": health.get("ifc_available"),
                "pdf_export_available": health.get("pdf_export_available"),
            },
            "concurrency_4x_zip": {
                "workers": args.concurrency,
                "used_dwg": use_dwg,
                "expect_pdf": expect_pdf,
                "wall_s": round(conc_wall_s, 3),
                "p95_s": round(conc_p95_s, 3),
                "max_s": round(max(conc_latencies), 3),
                "errors": len(conc_errors),
                "peak_rss_mib": round(peak_rss_conc, 2),
                "requests": bundle_results,
            },
            "soak_30_solves": {
                "iterations": args.soak_iters,
                "errors": soak_errors,
                "p95_s": round(_percentile(soak_latencies, 95.0), 3),
                "mean_s": round(sum(soak_latencies) / max(1, len(soak_latencies)), 3),
                "rss_warm_baseline_mib": round(rss_after_warmup, 2),
                "rss_soak_start_mib": round(rss_soak_start, 2),
                "rss_soak_end_mib": round(rss_soak_end, 2),
                "rss_growth_mib": rss_growth_mib,
            },
            "overall_peak_rss_mib": overall_peak_rss,
            "oom_killed": oom_killed,
        }

        out_json = json.dumps(report, indent=2)
        print(out_json)
        if args.report:
            Path(args.report).write_text(out_json, encoding="utf-8")

        failures: list[str] = []
        if oom_killed:
            failures.append("Container was OOMKilled under 2 GiB memory cap!")
        if conc_errors:
            failures.append(f"Concurrency ZIP errors: {conc_errors}")
        if conc_p95_s >= args.p95_ceiling_s:
            failures.append(
                f"4x parallel ZIP p95 ({conc_p95_s:.2f}s) exceeded {args.p95_ceiling_s}s ceiling"
            )
        if overall_peak_rss >= args.peak_rss_ceiling_mib:
            failures.append(
                f"Peak RSS ({overall_peak_rss:.1f} MiB) exceeded "
                f"{args.peak_rss_ceiling_mib} MiB ceiling"
            )
        if soak_errors > 0:
            failures.append(f"30-solve soak had {soak_errors} HTTP errors")
        if rss_growth_mib >= args.leak_ceiling_mib:
            failures.append(
                f"30-solve RSS growth ({rss_growth_mib:.1f} MiB) exceeded "
                f"{args.leak_ceiling_mib} MiB leak ceiling"
            )

        if failures:
            print("FAIL: Tier-2 Performance & Soak Suite:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 1

        print(
            f"PASS: Tier-2 Perf (4x ZIP p95={conc_p95_s:.2f}s, "
            f"peak RSS={overall_peak_rss:.1f} MiB, "
            f"30-solve soak RSS growth={rss_growth_mib:.1f} MiB, 0 OOM, 0 errors)"
        )
        return 0
    finally:
        stop_evt.set()
        if container_name:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)


if __name__ == "__main__":
    sys.exit(main())
