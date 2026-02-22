#!/usr/bin/env bash
set -euo pipefail

# Render manifest with actual Cloud Run URL.
# Usage:
#   CLOUD_RUN_URL=https://... ./render_manifest.sh > app-manifest.rendered.yaml

CLOUD_RUN_URL="${CLOUD_RUN_URL:-}"
if [[ -z "${CLOUD_RUN_URL}" ]]; then
  echo "CLOUD_RUN_URL is required" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sed "s#https://REPLACE_WITH_CLOUD_RUN_URL#${CLOUD_RUN_URL}#g" "${SCRIPT_DIR}/app-manifest.yaml"
