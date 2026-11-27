# CyElec Performance Testing & Capacity Engineering Procedure (SOP)

This document defines the established **3-Tier Cost-Safe Performance Testing Procedure**, Service Level Objectives (SLOs), profiling workflow, and memory/concurrency guardrails for **CyElec** — engineered to protect the **€0–€10/mo EU Cloud Run budget cap** (`prod_min_instances=0`, `2 vCPU / 2 GiB`, zero swap).

---

## 1. Performance SLOs & Hard Safety Ceilings

Hard safety ceilings automatically fail CI or block Production promotion. Aspirational targets are tracked in JSON telemetry artifacts (`perf_tier1_report.json`, `perf_tier2_report.json`) to catch gradual drift early.

| Tier | Workload / Probe | Hard-Fail Safety Ceiling | Aspirational Target | Verified Baseline | Cadence |
|---|---|---:|---:|---:|---|
| **Tier 1 (CI Python)** | `apartment_3bed` full DXF ingest + Phase 2 solve | `< 3.0 s` | `< 1.0 s` | `~0.23 s` | Every PR & `main` push |
| **Tier 1 (CI Python)** | `villa_2storey` Turnkey Phase 2 solve / Phase 3 export | `< 5.0 s` / `< 10.0 s` | `< 2.0 s` / `< 4.0 s` | `~0.15 s` / `~1.65 s` | Every PR & `main` push |
| **Tier 1 (CI Python)** | **48-Room / 4-Storey Large-CAD Stress Plan** (`720 m²`, 67 circuits, 757 devices) | `< 15.0 s` solve · `< 25.0 s` export · `0` stacked · `100%` lux · `0` DXF errors | `< 8.0 s` solve | `~3.02 s` solve / `~4.90 s` export | Every PR & `main` push |
| **Tier 1 (CI Python)** | Python peak `tracemalloc` (`apartment_3bed`) & OS RSS delta (48-room stress) | `< 250 MiB` | `< 120 MiB` | `~2.9 MiB` / `~13.8 MiB` | Every PR & `main` push |
| **Tier 2 (Docker `2 vCPU / 2 GiB`, zero swap)** | Single MIT binary `.dwg` + 12 WeasyPrint PDFs ZIP | `< 60.0 s` · `0` DXF errors | `< 25.0 s` | `~22.7 s` | `main` push + weekly + local |
| **Tier 2 (Docker `2 vCPU / 2 GiB`, zero swap)** | **4× Parallel Concurrent ZIP Downloads** | `0` OOM · `0` 5xx · p95 `< 90.0 s` · Peak RSS `< 1,500 MiB` | p95 `< 55.0 s` · RSS `< 1,100 MiB` | Enforced in CI | `main` push + weekly + local |
| **Tier 2 (Docker `2 vCPU / 2 GiB`, zero swap)** | **30-Solve Warm Memory-Leak Soak** (`.dwg`/`.ifc`/benchmarks) | `30/30` HTTP 200 · RSS growth `< 150 MiB` | `< 60 MiB` | `~0.3 MiB` (Python) | `main` push + weekly + local |
| **Tier 3 (Live EU Cloud Run)** | Cold-Start `/health` + first solve (`min_instances=0`) | `< 25.0 s` | `< 15.0 s` | Enforced per deploy | Every Staging & Prod deploy |
| **Tier 3 (Live EU Cloud Run)** | Warm Benchmark Solve p95 (3 warm calls) | `< 5.0 s` | `< 1.5 s` | `< 1.0 s` | Every Staging & Prod deploy + daily |

---

## 2. Running Performance Suites Locally

```bash
# Tier-1 (~13s): per-stage latency telemetry, tracemalloc, 48-room 4-storey large-CAD stress
./scripts/run_perf.sh --tier1

# Tier-2 (Docker 2 vCPU / 2 GiB, --memory-swap=2g): 4x parallel ZIP + 30-solve leak soak
./scripts/run_perf.sh --tier2

# Run both Tier-1 and Tier-2 sequentially
./scripts/run_perf.sh --all
```

---

## 3. Built-In Concurrency & Memory Hardening Architecture

To guarantee zero OOM kills and predictable tail latency on EU Cloud Run (`2 vCPU / 2 GiB`, zero swap):
1. **Bounded Heavy-Bundle Semaphore (`_BUNDLE_SEMAPHORE` in `src/cyelec/api/app.py`):**
   - Controlled via `CYELEC_MAX_CONCURRENT_BUNDLES` (default `2`, matching the container's `2 vCPU`).
   - Caps simultaneous Phase 3 DXF + 10× WeasyPrint PDF + ZIP pack generations so a 4× concurrent download burst runs cleanly as two 2-wide batches instead of spiking 4× Cairo/Pango memory trees into an OOM kill.
2. **Post-Bundle Cyclic Garbage Collection (`gc.collect()`):**
   - Explicitly invoked in `finally:` blocks after releasing `TemporaryDirectory` on all `/bundle` endpoints to promptly reclaim `ezdxf` entity graphs and WeasyPrint layout box cycles.
3. **O(N) Spatial Conduit Routing (`src/cyelec/spatial/router.py`):**
   - `_attach_node_to_graph` uses pure float arithmetic with exact AABB bounding-box lower-bound pruning (avoiding `O(E)` Pydantic instantiations).
   - `_order_waypoints_greedy` uses early-stopping exact Dijkstra (`_nearest_target_dijkstra`), keeping 48-room / 4-storey (757-device) routing at `~3.0 s` instead of `~19.6 s` with 0.00% engineering output diff.

---

## 4. Profiling & Bottleneck Remediation Workflow

If a Tier-1 or Tier-2 budget fails:
1. **Profile Tier-1 CPU Hotspots:**
   ```bash
   python3 -c "
   import cProfile, pstats
   from tests.perf.test_perf_budgets import _build_large_48_room_4storey_building
   from cyelec.pipeline import run_phase2_pipeline
   pr = cProfile.Profile()
   pr.enable()
   run_phase2_pipeline(_build_large_48_room_4storey_building())
   pr.disable()
   pstats.Stats(pr).sort_stats('cumtime').print_stats(25)
   "
   ```
2. **Inspect Tier-2 RSS Drift or Concurrency Spikes:**
   - Download `perf-tier2-report` artifact from the `docker-container-e2e` job (or `daily-monitor.yml` Sunday soak run).
   - Check `concurrency_4x_zip.peak_rss_mib`, `concurrency_4x_zip.p95_s`, and `soak_30_solves.rss_growth_mib`.
   - If `peak_rss_mib` approaches `1,400 MiB`, verify `CYELEC_MAX_CONCURRENT_BUNDLES=2` (or lower to `1` for ultra-tight RAM budgets) and inspect any newly retained module-level caches.
3. **Verify Zero Engineering Drift After Any Optimization:**
   - Every performance optimization MUST pass `pytest tests/regression/test_golden_regression.py -v` with **0% diff**.

---

## 5. €0-Budget Live Cloud Run Guardrails

- **Never** run heavy concurrency load tests or soak loops against live EU Cloud Run Staging or Production — all concurrency & soak load runs exclusively in **Tier-2 isolated Docker containers** (free GitHub Actions compute or local developer workstation).
- Live Tier-3 probes execute only lightweight cold-start + 3 warm solves on deploys and a **zero-write** `/health` + security-header + auth-gate probe once daily at `06:00 UTC` (`.github/workflows/daily-monitor.yml`), keeping monthly Cloud Run + Firestore spend strictly within the €0–€10/mo free-tier envelope.
