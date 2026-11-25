#!/usr/bin/env bash
# Create an idempotent €5/month GCP Billing Budget Alert scoped to a single project.
# Run once in Google Cloud Shell (requires Billing Account Owner/Costs Manager):
#   bash deploy/gcp/create_budget_alert.sh <GCP_PROJECT_ID> [AMOUNT_EUR=5]
set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null || true)}"
AMOUNT_EUR="${2:-5}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <GCP_PROJECT_ID> [AMOUNT_EUR=5]" >&2
  exit 1
fi

gcloud services enable billingbudgets.googleapis.com --project="${PROJECT_ID}" >/dev/null

BILLING_ACCOUNT="$(gcloud billing projects describe "${PROJECT_ID}" \
  --format='value(billingAccountName)' | sed 's|billingAccounts/||')"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
DISPLAY_NAME="CyElec ${PROJECT_ID} Monthly Cap (${AMOUNT_EUR} EUR)"

if [[ -z "${BILLING_ACCOUNT}" ]]; then
  echo "ERROR: No billing account linked to project ${PROJECT_ID}." >&2
  exit 1
fi

EXISTING="$(gcloud billing budgets list --billing-account="${BILLING_ACCOUNT}" \
  --filter="displayName='${DISPLAY_NAME}'" --format='value(name)' | head -n1)"

if [[ -n "${EXISTING}" ]]; then
  echo "Budget alert already exists: ${DISPLAY_NAME} (${EXISTING})"
  exit 0
fi

gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT}" \
  --display-name="${DISPLAY_NAME}" \
  --budget-amount="${AMOUNT_EUR}EUR" \
  --filter-projects="projects/${PROJECT_NUMBER}" \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0

echo "Created €${AMOUNT_EUR}/month budget alert (50%, 90%, 100% email notifications) for ${PROJECT_ID}."
