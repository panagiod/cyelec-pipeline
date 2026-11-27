#!/usr/bin/env bash
# CyElec Local Developer & Agent Quality Assurance Runner
# Usage:
#   ./scripts/run_qa.sh --quick   (lint, mypy, bandit, unit/integration/golden with >=85% coverage)
#   ./scripts/run_qa.sh --full    (--quick + pip-audit + Playwright UI + Tier-1 Perf & 48-room stress)
#   ./scripts/run_qa.sh --docker  (--full + Docker container E2E .dwg/.ifc/WeasyPrint + Tier-2 2vCPU/2GiB perf)
set -euo pipefail

MODE="${1:---quick}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "================================================================"
echo " CyElec QA Suite | Mode: ${MODE}"
echo "================================================================"

echo "[1/5] Lint (ruff)..."
ruff check src tests scripts

echo "[2/5] Static Type Check (mypy)..."
mypy src

echo "[3/5] Static Security SAST (bandit)..."
bandit -r src/cyelec -ll

echo "[4/5] Parallel Unit + Integration + Exact-Match Golden Suite (>=85% Coverage Gate)..."
pytest -n auto --cov=cyelec --cov-report=term-missing --cov-fail-under=85

if [[ "${MODE}" == "--quick" ]]; then
  echo "PASS: CyElec Quick QA Gate (ruff + mypy + bandit + pytest >=85% + exact-match golden)"
  exit 0
fi

echo "[5/8] Dependency CVE Audit (pip-audit)..."
pip-audit --skip-editable --desc=off

echo "[6/8] Playwright Browser UI Regression Suite (1440/768/390px + 5 sheets + 0 errors)..."
CYELEC_SCREENSHOT_DIR="${CYELEC_SCREENSHOT_DIR:-/tmp/cyelec_ui_screenshots}" \
  pytest tests/e2e/test_ui_playwright.py -v

echo "[7/8] Tier-1 Performance Budgets & 48-Room 4-Storey Large-CAD Stress Suite..."
CYELEC_PERF_REPORT="${CYELEC_PERF_REPORT:-/tmp/cyelec_perf_tier1.json}" \
  pytest tests/perf/test_perf_budgets.py -v

if [[ "${MODE}" == "--full" ]]; then
  echo "PASS: CyElec Full Local QA Gate (All Static, Coverage >=85%, Golden, Playwright UI & Tier-1 Perf)"
  exit 0
fi

if [[ "${MODE}" == "--docker" ]]; then
  echo "[8/8] Docker Production Container Integration (.dwg/.ifc/WeasyPrint) & Tier-2 2vCPU/2GiB Perf..."
  docker build -t cyelec-qa:local .
  CID="$(docker run -d --rm -p 127.0.0.1:18080:8080 \
    -e CYELEC_ENV=staging \
    -e CYELEC_AUTH_REQUIRED=false \
    -e CYELEC_ALLOW_ANONYMOUS=true \
    cyelec-qa:local)"
  trap 'docker rm -f "${CID}" >/dev/null 2>&1 || true' EXIT
  python3 scripts/e2e_docker_smoke.py --url http://127.0.0.1:18080 --report /tmp/cyelec_docker_e2e.json
  docker rm -f "${CID}" >/dev/null 2>&1 || true
  trap - EXIT

  python3 scripts/perf_docker_suite.py \
    --image cyelec-qa:local \
    --report /tmp/cyelec_perf_tier2.json
  echo "PASS: CyElec Complete Local + Docker Container QA & Tier-2 Perf Gate"
  exit 0
fi

echo "Unknown mode '${MODE}'. Use --quick, --full, or --docker." >&2
exit 2
