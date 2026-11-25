#!/usr/bin/env bash
# One-time Google Cloud bootstrap for keyless GitHub Actions CD (Workload Identity Federation).
# Run once in Google Cloud Shell:
#   bash deploy/gcp/bootstrap_wif.sh <GCP_PROJECT_ID> [GITHUB_REPO=panagiod/cyelec-pipeline]
set -euo pipefail

PROJECT_ID="${1:-}"
GITHUB_REPO="${2:-panagiod/cyelec-pipeline}"
REGION="${GCP_REGION:-europe-west1}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <GCP_PROJECT_ID> [owner/repo]" >&2
  exit 1
fi

echo "==> Configuring GCP project: ${PROJECT_ID} (region: ${REGION}, repo: ${GITHUB_REPO})"
gcloud config set project "${PROJECT_ID}"

echo "==> 1/6 Enabling required Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
TF_STATE_BUCKET="${PROJECT_ID}-cyelec-tfstate"
AR_REPO="cyelec"
WIF_POOL="github-actions-pool"
WIF_PROVIDER="github-oidc"
DEPLOYER_SA_ID="cyelec-gh-deployer"
DEPLOYER_SA="${DEPLOYER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
ADMIN_SECRET_ID="cyelec-prod-admin-key"

echo "==> 2/6 Creating EU Artifact Registry repo (${REGION}/${AR_REPO})..."
if ! gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="cyelec-pipeline EU container images"
fi

echo "==> 3/6 Creating EU Terraform remote state bucket (gs://${TF_STATE_BUCKET})..."
if ! gcloud storage buckets describe "gs://${TF_STATE_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${TF_STATE_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
  gcloud storage buckets update "gs://${TF_STATE_BUCKET}" --versioning
fi

echo "==> 4/6 Provisioning prod CYELEC_ADMIN_KEY in GCP Secret Manager..."
if ! gcloud secrets describe "${ADMIN_SECRET_ID}" >/dev/null 2>&1; then
  gcloud secrets create "${ADMIN_SECRET_ID}" \
    --replication-policy="user-managed" \
    --locations="${REGION}"
  ADMIN_KEY="$(python3 -c 'import secrets; print("cyelec_admin_" + secrets.token_urlsafe(32))')"
  printf '%s' "${ADMIN_KEY}" | gcloud secrets versions add "${ADMIN_SECRET_ID}" --data-file=-
  echo "    Generated new ${ADMIN_SECRET_ID} in Secret Manager (never stored in git)."
fi

echo "==> 5/6 Creating deployer Service Account & least-privilege roles..."
if ! gcloud iam service-accounts describe "${DEPLOYER_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOYER_SA_ID}" \
    --display-name="cyelec-pipeline GitHub Actions Keyless Deployer"
fi

for ROLE in \
  roles/run.admin \
  roles/datastore.owner \
  roles/storage.admin \
  roles/artifactregistry.writer \
  roles/iam.serviceAccountAdmin \
  roles/iam.serviceAccountUser \
  roles/resourcemanager.projectIamAdmin \
  roles/secretmanager.admin; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="${ROLE}" \
    --quiet >/dev/null
done

echo "==> 6/6 Configuring Keyless Workload Identity Federation (OIDC) for ${GITHUB_REPO}..."
if ! gcloud iam workload-identity-pools describe "${WIF_POOL}" --location="global" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${WIF_POOL}" \
    --location="global" \
    --display-name="GitHub Actions Pool"
fi

if ! gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER}" \
    --location="global" --workload-identity-pool="${WIF_POOL}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER}" \
    --location="global" \
    --workload-identity-pool="${WIF_POOL}" \
    --display-name="GitHub Actions OIDC Provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
    --issuer-uri="https://token.actions.githubusercontent.com"
fi

WIF_PROVIDER_FULL="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"

gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPO}" \
  --quiet >/dev/null

echo ""
echo "========================================================================"
echo "Bootstrap complete! Set these 3 non-secret GitHub Actions variables:"
echo "========================================================================"
echo "gh variable set GCP_PROJECT_ID   -R ${GITHUB_REPO} --body '${PROJECT_ID}'"
echo "gh variable set GCP_WIF_PROVIDER -R ${GITHUB_REPO} --body '${WIF_PROVIDER_FULL}'"
echo "gh variable set GCP_DEPLOYER_SA  -R ${GITHUB_REPO} --body '${DEPLOYER_SA}'"
echo ""
echo "Then Antigravity can deploy staging or prod with zero secrets:"
echo "  gh workflow run deploy-gcp.yml -R ${GITHUB_REPO} -f environment=staging"
echo "  gh workflow run deploy-gcp.yml -R ${GITHUB_REPO} -f environment=prod"
echo "========================================================================"
