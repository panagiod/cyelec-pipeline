# CyElec Quality Assurance & Regression Testing Procedure (SOP)

This document defines the mandatory Quality Assurance, Regression Testing, Release Promotion, and Automatic Rollback standard operating procedure for **CyElec** (Cyprus BS 7671 / CYS HD 384 / EAC Turnkey Electrical Engineering Platform).

---

## 1. Six-Tier QA & Regression Gate Matrix

Every change to `cyelec-pipeline` must clear all applicable gates before reaching live EU Production (`cyelec-prod`, `europe-west1`).

| Tier | Gate Name | Scope & Enforced Invariants | Cadence | Runner / Workflow |
|---|---|---|---|---|
| **Tier 1** | **Static Quality & Security** | `ruff check src tests scripts`, `mypy src`, `bandit -r src/cyelec -ll`, `pip-audit --skip-editable` | Every PR & `main` push | `.github/workflows/ci.yml` (`quality-unit-golden`) |
| **Tier 2** | **Unit, Integration & Coverage Gate** | Full `pytest -n auto --cov=cyelec --cov-fail-under=85` (hard **85%** line coverage floor; baseline ~92%) + CLI E2E smoke | Every PR & `main` push | `.github/workflows/ci.yml` (`quality-unit-golden`) |
| **Tier 3** | **Exact-Match Golden Engineering Regression (`0%` Tolerance)** | Deterministic exact-match diff across 5 Cyprus benchmarks (`apartment_3bed`, `villa_pv_ev_vrv`, `villa_2storey`, `villa_2storey_turnkey`, `commercial_office`) covering supply tier, diversified kVA, phase imbalance, every circuit (`breaker`, `cable`, `length_m`, `voltage_drop_pct`, `phase`, `overall_pass`), BOQ totals, `0` stacked devices (`<100 mm`), `100%` CIBSE lux compliance, `0` DXF audit errors, `0` E-201 overlaps, scoped E-301 CSS, `0` literal `>None<` | Every PR & `main` push | `tests/regression/test_golden_regression.py` |
| **Tier 4** | **Playwright Browser UI Regression** | Real headless Chromium (`1440×900`, `768×1024`, `390×844`): all 5 sheets (`E-101..E-301`), circuit override + Apply, 60s Brief, Unpriced Tender BOQ toggle, interactive DB1 click-relocate, Zoom/Fullscreen/ESC, `E-301` white header `rgb(255,255,255)` computed DOM check, DXF upload + ZIP download + Clear File, `/guide`, `0` console/page/HTTP errors, screenshot artifacts | Every PR, `main` push & Live Staging deploy | `tests/e2e/test_ui_playwright.py` |
| **Tier 5** | **Production Docker Container Integration** | Builds production `Dockerfile` (GHA cached) and verifies native binaries using 100% MIT-original fixtures (`tests/fixtures/cad/cyprus_minimal_house.dwg` & `.ifc`): LibreDWG `dwg2dxf`, IfcOpenShell IFC4 BIM, WeasyPrint (`>= 10` real `%PDF-` files), `0` `ezdxf` audit errors, OWASP headers; on `main` push also runs Tier-2 `2 vCPU / 2 GiB` concurrency & memory-soak suite | Every PR & `main` push | `scripts/e2e_docker_smoke.py` + `scripts/perf_docker_suite.py` |
| **Tier 6** | **Live EU Cloud Run Staging → Auto-Promote Prod + Auto-Rollback** | Triggered strictly after `CI` succeeds on `main`: builds **one** immutable SHA image → deploys EU Staging → runs Live Staging E2E (`.dwg`/`.ifc`/multi-floor/12 PDFs/Tier-3 latency) + Live Playwright UI → auto-promotes exact same SHA image to EU Production → runs Live Prod E2E (auth, password/API-key rotation, branding + 5% VAT, free-plan seat limit, 3-rev carry-forward `diversified_kva_delta > +5.0`, branded unpriced ZIP, cross-tenant `404` isolation, GDPR Art. 20 export & guaranteed `finally` GDPR Art. 17 erasure + post-erasure `401`). If Prod verification fails → **automatic instant Cloud Run traffic rollback to `PREV_REV`** | Every green `main` CI run + daily `06:00 UTC` zero-write monitor | `.github/workflows/deploy-gcp.yml` & `.github/workflows/daily-monitor.yml` |

---

## 2. Local Developer & Agent Commands

Run locally before opening a PR or pushing to `main`:

```bash
# Fast local loop (~20s): lint, mypy, bandit, parallel pytest (>=85% cov) + exact-match golden
./scripts/run_qa.sh --quick

# Full local verification (~60s): adds pip-audit, Playwright Chromium UI & Tier-1 48-room perf
./scripts/run_qa.sh --full

# Full container parity (requires local Docker daemon): builds Dockerfile + MIT .dwg/.ifc/PDF + Tier-2 2vCPU/2GiB perf
./scripts/run_qa.sh --docker
```

---

## 3. Exact-Match Golden Engineering Baseline Policy (`0%` Tolerance)

Golden snapshots live in `tests/fixtures/golden_baselines.json` and are enforced with **zero tolerance (`==`)** by `tests/regression/test_golden_regression.py`.

### When a Golden Diff Fails
1. **Unexpected diff (regression):** Fix the code bug immediately. **Never** blindly regenerate baselines to silence a failing test!
2. **Intentional engineering, standards YAML, or BOQ rate change:**
   - Review the exact field-by-field diff reported by pytest.
   - Regenerate snapshots explicitly:
     ```bash
     python3 scripts/update_golden.py
     git diff tests/fixtures/golden_baselines.json
     ```
   - Inspect every changed line in `git diff tests/fixtures/golden_baselines.json` and document the regulatory/engineering justification in the PR description (e.g., *"Updated BS 7671 Table 4D5 derating or Cyprus 2026 copper rate — `apartment_3bed` BOQ +€142.50"*).
   - Re-run `pytest tests/regression/test_golden_regression.py -v` to confirm `5 passed`.

---

## 4. License-Clean Original CAD Fixtures Policy

- Every CAD test fixture committed under `tests/fixtures/cad/` (`cyprus_minimal_house.dwg`, `cyprus_minimal_house.ifc`) is **100% MIT-original** synthesized geometry with zero third-party or client copyright claims.
- **Zero AGPLv3 policy:** LibreDWG (`dwg2dxf`) is executed strictly as an isolated OS subprocess (never linked into Python). WeasyPrint (BSD) and IfcOpenShell (LGPLv3) remain optional extras installed only in the container runtime.

---

## 5. Debugging Playwright UI Failures in CI

When `playwright-ui` fails in GitHub Actions:
1. Open the failed workflow run in GitHub Actions → **Artifacts** → download `playwright-ui-screenshots` (uploaded `if: always()`).
2. Inspect desktop (`1440px`), tablet (`768px`), mobile (`390px`), `E-201` SLD, and `E-301` Details captures.
3. Reproduce locally against local uvicorn or live Staging:
   ```bash
   CYELEC_SCREENSHOT_DIR=/tmp/ui_debug pytest tests/e2e/test_ui_playwright.py -v
   # Or against live EU Staging:
   CYELEC_E2E_URL=https://cyelec-staging-hym54lfrsq-ew.a.run.app pytest tests/e2e/test_ui_playwright.py -v
   ```

---

## 6. Production Automatic Rollback & Incident Runbook

In `.github/workflows/deploy-gcp.yml`:
1. Before `terraform apply` on `cyelec-prod`, the workflow records the currently serving revision (`PREV_REV`).
2. After `terraform apply`, `scripts/e2e_live_ops.py --env prod` executes the full live SaaS & Tier-3 latency suite (always cleaning up ephemeral QA tenants via GDPR Art. 17 `DELETE /v1/tenants/me` in a `finally:` block).
3. If **any** check fails, the `if: failure()` rollback step immediately routes 100% of Production traffic back to `PREV_REV`:
   ```bash
   gcloud run services update-traffic cyelec-prod \
     --region europe-west1 \
     --to-revisions="${PREV_REV}=100"
   ```
4. Manual emergency rollback (if ever needed outside CI):
   ```bash
   gcloud run revisions list --service cyelec-prod --region europe-west1 --limit 5
   gcloud run services update-traffic cyelec-prod --region europe-west1 --to-revisions="<GOOD_REV>=100"
   ```

---

## 7. PR & Standards-Change Review Checklist

- [ ] `./scripts/run_qa.sh --full` passes locally with `0` warnings/errors and coverage `>= 85%`.
- [ ] Any modification to `standards/*.yaml` includes verified BS 7671 / CYS HD 384 / EAC regulation citations and reviewed `git diff tests/fixtures/golden_baselines.json`.
- [ ] Any UI change in `src/cyelec/api/ui.py` passes `tests/e2e/test_ui_playwright.py` across `1440px`, `768px`, and `390px` with `0` console/page/HTTP errors.
- [ ] Cloud Run budget guardrails remain untouched (`prod_min_instances=0`, `budget_gate_mode=pause_ingress`, Artifact Registry keep-2 pruning).
