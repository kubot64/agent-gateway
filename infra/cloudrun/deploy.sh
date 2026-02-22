#!/usr/bin/env bash
set -euo pipefail

# Cloud Run deploy helper for home-secretary-backend.
# Required env vars:
#   GCP_PROJECT
# Optional env vars:
#   GCP_REGION (default: asia-northeast1)
#   SERVICE_NAME (default: home-agent-api)
#   AR_REPO (default: home)
#   IMAGE_NAME (default: home-agent-api)
#   DATABASE_BACKEND (default: postgres)
#   IDEMPOTENCY_BACKEND (default: postgres)
#   DOC_SEARCH_BACKEND (default: keyword)
#   MIN_INSTANCES (default: 0)
#   MAX_INSTANCES (default: 3)
#   CPU (default: 1)
#   MEMORY (default: 512Mi)
#   ALLOW_UNAUTHENTICATED (default: true)
#
# Required Secret Manager secrets (latest version is used):
#   postgres-dsn
#   slack-signing-secret
#   slack-bot-token
#   discord-public-key
# Optional secret:
#   openai-api-key

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

require_cmd gcloud

GCP_PROJECT="${GCP_PROJECT:-}"
if [[ -z "${GCP_PROJECT}" ]]; then
  echo "GCP_PROJECT is required" >&2
  exit 1
fi

GCP_REGION="${GCP_REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-home-agent-api}"
AR_REPO="${AR_REPO:-home}"
IMAGE_NAME="${IMAGE_NAME:-home-agent-api}"
DATABASE_BACKEND="${DATABASE_BACKEND:-postgres}"
IDEMPOTENCY_BACKEND="${IDEMPOTENCY_BACKEND:-postgres}"
DOC_SEARCH_BACKEND="${DOC_SEARCH_BACKEND:-keyword}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-3}"
CPU="${CPU:-1}"
MEMORY="${MEMORY:-512Mi}"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-true}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE_URI="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/${IMAGE_NAME}:$(date +%Y%m%d-%H%M%S)"

echo "[1/6] configure project"
gcloud config set project "${GCP_PROJECT}" >/dev/null

echo "[2/6] enable required APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com >/dev/null

echo "[3/6] ensure artifact registry repo (${AR_REPO})"
if ! gcloud artifacts repositories describe "${AR_REPO}" --location "${GCP_REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format docker \
    --location "${GCP_REGION}" \
    --description "home images"
fi

echo "[4/6] build and push image"
gcloud builds submit "${REPO_ROOT}" --tag "${IMAGE_URI}"

echo "[5/6] deploy cloud run service"
set_secrets="POSTGRES_DSN=postgres-dsn:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest,SLACK_BOT_TOKEN=slack-bot-token:latest,DISCORD_PUBLIC_KEY=discord-public-key:latest"
if gcloud secrets describe openai-api-key >/dev/null 2>&1; then
  set_secrets="${set_secrets},OPENAI_API_KEY=openai-api-key:latest"
fi

unauth_flag="--allow-unauthenticated"
if [[ "${ALLOW_UNAUTHENTICATED}" != "true" ]]; then
  unauth_flag="--no-allow-unauthenticated"
fi

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_URI}" \
  --region "${GCP_REGION}" \
  --platform managed \
  --port 8000 \
  --cpu "${CPU}" \
  --memory "${MEMORY}" \
  --min-instances "${MIN_INSTANCES}" \
  --max-instances "${MAX_INSTANCES}" \
  --set-env-vars "DATABASE_BACKEND=${DATABASE_BACKEND},IDEMPOTENCY_BACKEND=${IDEMPOTENCY_BACKEND},DOC_SEARCH_BACKEND=${DOC_SEARCH_BACKEND}" \
  --set-secrets "${set_secrets}" \
  ${unauth_flag}

echo "[6/6] resolve service URL"
SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --region "${GCP_REGION}" --format='value(status.url)')"

echo "deployed: ${SERVICE_URL}"
echo "slack events: ${SERVICE_URL}/api/inbound/slack/events"
echo "slack commands: ${SERVICE_URL}/api/inbound/slack/commands"
echo "slack interactions: ${SERVICE_URL}/api/inbound/slack/interactions"
echo "discord interactions: ${SERVICE_URL}/api/inbound/discord/interactions"
