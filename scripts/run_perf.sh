#!/usr/bin/env bash
# CyElec Local Performance Testing Runner (3-Tier Cost-Safe Procedure)
# Usage:
#   ./scripts/run_perf.sh --tier1  (Per-stage budgets, tracemalloc, 48-room 4-storey stress)
#   ./scripts/run_perf.sh --tier2  (Docker 2 vCPU / 2 GiB 4x parallel ZIP + 30-solve leak soak)
#   ./scripts/run_perf.sh --all    (Tier 1 + Tier 2)
set -euo pipefail

MODE="${1:---tier1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

run_tier1() {
  echo "================================================================"
  echo " Tier-1 Performance Budgets & 48-Room 4-Storey Stress Suite"
  echo "================================================================"
  CYELEC_PERF_REPORT="${CYELEC_PERF_REPORT:-/tmp/cyelec_perf_tier1.json}" \
    pytest tests/perf/test_perf_budgets.py -v
  cat "${CYELEC_PERF_REPORT:-/tmp/cyelec_perf_tier1.json}"
}

run_tier2() {
  echo "================================================================"
  echo " Tier-2 Docker (2 vCPU / 2 GiB) 4x Parallel ZIP & 30-Solve Soak"
  echo "================================================================"
  docker build -t cyelec-perf:local .
  python3 scripts/perf_docker_suite.py \
    --image cyelec-perf:local \
    --report "${CYELEC_PERF_TIER2_REPORT:-/tmp/cyelec_perf_tier2.json}"
}

case "${MODE}" in
  --tier1)
    run_tier1
    ;;
  --tier2)
    run_tier2
    ;;
  --all)
    run_tier1
    run_tier2
    ;;
  *)
    echo "Unknown mode '${MODE}'. Use --tier1, --tier2, or --all." >&2
    exit 2
    ;;
esac
