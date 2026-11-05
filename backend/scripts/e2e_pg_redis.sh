#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
INFRA_DIR="${ROOT_DIR}/infra"
PORT="${PORT:-18000}"
E2E_MODE="${E2E_MODE:-auto}" # auto | host | docker
E2E_CLEANUP="${E2E_CLEANUP:-0}" # 1 to also remove the E2E postgres + redis on exit (the test app is always removed)
BASE_URL=""
APP_PID=""
DOCKER_APP_STARTED=0
# 手元の docker-compose.yml とは別のプロジェクト・ポート・データで動かし、既存のサービスに触れない。
E2E_PROJECT="${E2E_PROJECT:-agent-gateway-e2e}"
export E2E_APP_PORT="${PORT}"
export E2E_POSTGRES_PORT="${E2E_POSTGRES_PORT:-15432}"
export E2E_REDIS_PORT="${E2E_REDIS_PORT:-16379}"
# handle-message の直接入口は既定で無効。E2E では依頼を直接送るために有効にする。
export ENABLE_DIRECT_HANDLE_MESSAGE=1
# Slack の入口は署名鍵がないと 401 を返す。E2E 用の鍵で起動し、送るリクエストに署名する。
export SLACK_SIGNING_SECRET="e2e-slack-signing-secret"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd curl
require_cmd python3

compose() {
  docker compose -p "${E2E_PROJECT}" -f "${INFRA_DIR}/docker-compose.e2e.yml" "$@"
}

echo "[1/6] starting postgres + redis via docker compose (project: ${E2E_PROJECT})"
compose up -d --wait

cleanup() {
  if [[ -n "${APP_PID}" ]]; then
    kill "${APP_PID}" >/dev/null 2>&1 || true
  fi
  # テスト用アプリは直接入口を開けて起動しているので、E2E_CLEANUP に関係なく必ず止める。
  # 対象は E2E 専用プロジェクトの app だけなので、手元で動いているアプリは止まらない。
  if [[ "${DOCKER_APP_STARTED}" == "1" ]]; then
    compose --profile app rm --stop --force app >/dev/null 2>&1 || true
  fi
  if [[ "${E2E_CLEANUP}" == "1" ]]; then
    compose --profile app down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Slack の署名を付けて JSON を POST する。$1: path, $2: body, $3: 応答の保存先
post_slack_signed() {
  local path="$1" body="$2" out="$3" ts sig
  ts="$(date +%s)"
  sig="$(python3 - "${SLACK_SIGNING_SECRET}" "${ts}" "${body}" <<'PY'
import hashlib, hmac, sys
secret, ts, body = sys.argv[1], sys.argv[2], sys.argv[3]
base = f"v0:{ts}:".encode("utf-8") + body.encode("utf-8")
print("v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest())
PY
)"
  curl -sS -X POST "${BASE_URL}${path}" \
    -H "content-type: application/json" \
    -H "X-Slack-Request-Timestamp: ${ts}" \
    -H "X-Slack-Signature: ${sig}" \
    --data-binary "${body}" >"${out}"
}

has_host_python_deps() {
  python3 - <<'PY'
import importlib.util
mods = ["fastapi", "uvicorn", "psycopg", "redis"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    raise SystemExit(1)
PY
}

start_host_api() {
  echo "[2/6] host mode selected, validating local python dependencies"
  if ! has_host_python_deps; then
    echo "missing python packages for host mode: fastapi, uvicorn, psycopg, redis" >&2
    exit 1
  fi
  echo "[3/6] starting host API with postgres+redis backend"
  export DATABASE_BACKEND=postgres
  export POSTGRES_DSN="${POSTGRES_DSN:-postgresql://home:home@localhost:${E2E_POSTGRES_PORT}/home}"
  export IDEMPOTENCY_BACKEND=redis
  export REDIS_URL="${REDIS_URL:-redis://localhost:${E2E_REDIS_PORT}/0}"
  cd "${BACKEND_DIR}"
  PYTHONPATH=src python3 -m uvicorn gateway.main:app --host 127.0.0.1 --port "${PORT}" >/tmp/home-e2e-api.log 2>&1 &
  APP_PID=$!
  BASE_URL="http://127.0.0.1:${PORT}"
}

start_docker_api() {
  echo "[2/6] docker mode selected, starting compose app profile"
  compose --profile app build app
  compose --profile app up -d app
  DOCKER_APP_STARTED=1
  BASE_URL="http://127.0.0.1:${E2E_APP_PORT}"
}

if [[ "${E2E_MODE}" == "host" ]]; then
  start_host_api
elif [[ "${E2E_MODE}" == "docker" ]]; then
  start_docker_api
else
  echo "[2/6] auto mode: choosing host or docker API runtime"
  if has_host_python_deps; then
    start_host_api
  else
    echo "host python deps not found; fallback to docker API runtime"
    start_docker_api
  fi
fi

echo "[4/6] waiting for health endpoint"
for _ in $(seq 1 30); do
  if curl -sS "${BASE_URL}/api/health" >/tmp/home-health.json; then
    break
  fi
  sleep 1
done

echo "[5/6] running endpoint checks"
ASK_RESP="$(curl -sS -X POST "${BASE_URL}/api/agent/handle-message" \
  -H "content-type: application/json" \
  -d '{"platform":"api","workspace_id":"W1","channel_id":"C1","user_id":"U1","text":"/ask サマリして"}')"
python3 - <<'PY' "${ASK_RESP}"
import json, sys
obj = json.loads(sys.argv[1])
assert obj["status"] == "completed", obj
PY

REMIND_RESP="$(curl -sS -X POST "${BASE_URL}/api/agent/handle-message" \
  -H "content-type: application/json" \
  -d '{"platform":"api","workspace_id":"W1","channel_id":"C1","user_id":"U1","text":"/remind 2026-03-01T09:00 all members"}')"
CONFIRM_ID="$(python3 - <<'PY' "${REMIND_RESP}"
import json, sys
obj = json.loads(sys.argv[1])
assert obj["status"] == "needs_confirmation", obj
print(obj["metadata"]["confirmation_id"])
PY
)"

# 認証なしで承認できた confirm-action の入口は廃止されている。
CONFIRM_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${BASE_URL}/api/agent/confirm-action" \
  -H "content-type: application/json" \
  -d "{\"confirmation_id\":\"${CONFIRM_ID}\",\"approved\":true}")"
if [[ "${CONFIRM_STATUS}" != "404" ]]; then
  echo "confirm-action should be gone, got HTTP ${CONFIRM_STATUS}" >&2
  exit 1
fi

DUP_EVENT_ID="evt-e2e-dup-$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
)"
DUP_PAYLOAD="{\"event_id\":\"${DUP_EVENT_ID}\",\"team_id\":\"T1\",\"event\":{\"ts\":\"1700000000\",\"channel\":\"C1\",\"user\":\"U1\",\"text\":\"/ask hello\"}}"
# 署名のないイベントは 401 で拒否される。
UNSIGNED_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${BASE_URL}/api/inbound/slack/events" \
  -H "content-type: application/json" -d "${DUP_PAYLOAD}")"
if [[ "${UNSIGNED_STATUS}" != "401" ]]; then
  echo "unsigned slack event should be rejected, got HTTP ${UNSIGNED_STATUS}" >&2
  exit 1
fi
post_slack_signed /api/inbound/slack/events "${DUP_PAYLOAD}" /tmp/home-dup1.json
post_slack_signed /api/inbound/slack/events "${DUP_PAYLOAD}" /tmp/home-dup2.json
python3 - <<'PY'
import json
with open("/tmp/home-dup1.json", "r", encoding="utf-8") as f:
    first = json.load(f)
with open("/tmp/home-dup2.json", "r", encoding="utf-8") as f:
    second = json.load(f)
assert first["status"] in {"completed", "duplicate"}, first
assert second["status"] == "duplicate", second
PY

echo "[6/6] E2E passed"
