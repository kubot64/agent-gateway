#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
INFRA_DIR="${ROOT_DIR}/infra"
PORT="${PORT:-18000}"
E2E_MODE="${E2E_MODE:-auto}" # auto | host | docker
E2E_CLEANUP="${E2E_CLEANUP:-0}" # 1 to stop compose stack on exit
BASE_URL=""
APP_PID=""

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd curl
require_cmd python3

echo "[1/6] starting postgres + redis via docker compose"
docker compose -f "${INFRA_DIR}/docker-compose.yml" up -d

cleanup() {
  if [[ -n "${APP_PID}" ]]; then
    kill "${APP_PID}" >/dev/null 2>&1 || true
  fi
  if [[ "${E2E_CLEANUP}" == "1" ]]; then
    docker compose -f "${INFRA_DIR}/docker-compose.yml" --profile app down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

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
  export POSTGRES_DSN="${POSTGRES_DSN:-postgresql://home:home@localhost:5432/home}"
  export IDEMPOTENCY_BACKEND=redis
  export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
  cd "${BACKEND_DIR}"
  PYTHONPATH=src python3 -m uvicorn api.app:app --host 127.0.0.1 --port "${PORT}" >/tmp/home-e2e-api.log 2>&1 &
  APP_PID=$!
  BASE_URL="http://127.0.0.1:${PORT}"
}

start_docker_api() {
  echo "[2/6] docker mode selected, starting compose app profile"
  docker compose -f "${INFRA_DIR}/docker-compose.yml" --profile app up -d --build
  BASE_URL="http://127.0.0.1:8000"
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

curl -sS -X POST "${BASE_URL}/api/agent/confirm-action" \
  -H "content-type: application/json" \
  -d "{\"confirmation_id\":\"${CONFIRM_ID}\",\"approved\":true}" >/tmp/home-confirm.json

DUP_EVENT_ID="evt-e2e-dup-$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
)"
DUP_PAYLOAD="{\"event_id\":\"${DUP_EVENT_ID}\",\"team_id\":\"T1\",\"event\":{\"ts\":\"1700000000\",\"channel\":\"C1\",\"user\":\"U1\",\"text\":\"/ask hello\"}}"
curl -sS -X POST "${BASE_URL}/api/inbound/slack/events" -H "content-type: application/json" -d "${DUP_PAYLOAD}" >/tmp/home-dup1.json
curl -sS -X POST "${BASE_URL}/api/inbound/slack/events" -H "content-type: application/json" -d "${DUP_PAYLOAD}" >/tmp/home-dup2.json
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
