#!/usr/bin/env bash
set -euo pipefail

# Register global slash commands for Discord application.
# Required env vars:
#   DISCORD_APP_ID
#   DISCORD_BOT_TOKEN

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq

DISCORD_APP_ID="${DISCORD_APP_ID:-}"
DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN:-}"

if [[ -z "${DISCORD_APP_ID}" || -z "${DISCORD_BOT_TOKEN}" ]]; then
  echo "DISCORD_APP_ID and DISCORD_BOT_TOKEN are required" >&2
  exit 1
fi

API="https://discord.com/api/v10/applications/${DISCORD_APP_ID}/commands"

payload='[
  {
    "name": "ask",
    "description": "Ask assistant",
    "type": 1,
    "options": [
      {"name": "question", "description": "question text", "type": 3, "required": true}
    ]
  },
  {
    "name": "summarize",
    "description": "Summarize thread",
    "type": 1,
    "options": [
      {"name": "target", "description": "current_thread", "type": 3, "required": false}
    ]
  },
  {
    "name": "remind",
    "description": "Create reminder",
    "type": 1,
    "options": [
      {"name": "datetime", "description": "ISO8601 datetime (e.g. 2026-03-01T09:00)", "type": 3, "required": true},
      {"name": "message", "description": "reminder message", "type": 3, "required": true}
    ]
  },
  {
    "name": "task",
    "description": "Create task",
    "type": 1,
    "options": [
      {"name": "action", "description": "add", "type": 3, "required": true, "choices": [{"name": "add", "value": "add"}]},
      {"name": "title", "description": "task title", "type": 3, "required": true}
    ]
  },
  {
    "name": "mode",
    "description": "Set response mode",
    "type": 1,
    "options": [
      {"name": "value", "description": "brief or detailed", "type": 3, "required": true, "choices": [{"name": "brief", "value": "brief"}, {"name": "detailed", "value": "detailed"}]}
    ]
  }
]'

echo "registering commands for app ${DISCORD_APP_ID}"
response="$(curl -sS -X PUT "${API}" \
  -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${payload}")"

if echo "${response}" | jq -e 'type == "array"' >/dev/null 2>&1; then
  echo "registered: $(echo "${response}" | jq 'length') commands"
  echo "ok"
else
  echo "failed: ${response}" >&2
  exit 1
fi
