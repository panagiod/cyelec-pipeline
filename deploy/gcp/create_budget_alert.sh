#!/usr/bin/env bash
# Wire a €10/month GCP Billing Budget directly to the automated `cyelec-budget-gate` Pub/Sub topic.
# At 90% (€9.00), `cyelec-budget-gate` automatically flips cyelec-staging & cyelec-prod ingress
# to INGRESS_TRAFFIC_INTERNAL_ONLY (reversible hard stop; all Firestore/GCS data preserved).
#
# Run once in Google Cloud Shell:
#   bash deploy/gcp/create_budget_alert.sh <GCP_PROJECT_ID> [AMOUNT_EUR=10]
set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null || true)}"
AMOUNT_EUR="${2:-10}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <GCP_PROJECT_ID> [AMOUNT_EUR=10]" >&2
  exit 1
fi

gcloud services enable billingbudgets.googleapis.com pubsub.googleapis.com \
  --project="${PROJECT_ID}" >/dev/null

BILLING_ACCOUNT="$(gcloud billing projects describe "${PROJECT_ID}" \
  --format='value(billingAccountName)' | sed 's|billingAccounts/||')"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
TOPIC_PATH="projects/${PROJECT_ID}/topics/cyelec-budget-gate"
DISPLAY_NAME="CyElec ${PROJECT_ID} Hard Gate (${AMOUNT_EUR} EUR)"

if [[ -z "${BILLING_ACCOUNT}" ]]; then
  echo "ERROR: No billing account linked to project ${PROJECT_ID}." >&2
  exit 1
fi

# Ensure Pub/Sub topic exists (normally created by Terraform prod apply)
if ! gcloud pubsub topics describe cyelec-budget-gate --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud pubsub topics create cyelec-budget-gate --project="${PROJECT_ID}"
fi

EXISTING="$(gcloud billing budgets list --billing-account="${BILLING_ACCOUNT}" \
  --filter="displayName='${DISPLAY_NAME}'" --format='value(name)' | head -n1)"

if [[ -n "${EXISTING}" ]]; then
  echo "Hard-gate budget already exists: ${DISPLAY_NAME} (${EXISTING})"
  exit 0
fi

gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT}" \
  --display-name="${DISPLAY_NAME}" \
  --budget-amount="${AMOUNT_EUR}EUR" \
  --filter-projects="projects/${PROJECT_NUMBER}" \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0 \
  --notifications-rule-pubsub-topic="${TOPIC_PATH}"

echo "========================================================================"
echo "ACTIVATED €${AMOUNT_EUR}/month HARD GATE for ${PROJECT_ID}!"
echo "  - 50% (€$((AMOUNT_EUR / 2))): Email warning"
echo "  - 90% (€$((AMOUNT_EUR * 9 / 10))): Pub/Sub -> cyelec-budget-gate pauses public Cloud Run ingress!"
echo "  - 100% (€${AMOUNT_EUR}): Final notification"
echo "========================================================================"
