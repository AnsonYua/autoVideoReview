#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
elif command -v py >/dev/null 2>&1; then
  PYTHON_CMD=(py -3)
else
  echo "Python not found. Install python3, python, or py." >&2
  exit 1
fi

if [[ -f ".env.local" ]]; then
  # shellcheck disable=SC1091
  source ".env.local"
fi

export TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-8531053205:AAGuLjFSrfWgAqwrxDzMoGP1YEf_Z5OkuFs}"
export TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-8682734076}"
export CODEX_COMMAND="${CODEX_COMMAND:-codex}"
export COMFYUI_BASE_URL="${COMFYUI_BASE_URL:-http://127.0.0.1:8188}"
export COMFYUI_WORKFLOW_PATH="${COMFYUI_WORKFLOW_PATH:-$ROOT_DIR/workflow.json}"
export WORKFLOW_STORAGE_PATH="${WORKFLOW_STORAGE_PATH:-$ROOT_DIR/data/projects.json}"
export PROJECT_ID="${PROJECT_ID:-project_c022d8962864}"

usage() {
  cat <<EOF
Usage:
  ./start_run.sh get-updates
  ./start_run.sh import <project_file>
  ./start_run.sh run [project_id]

Required env:
  TELEGRAM_BOT_TOKEN

Optional env:
  TELEGRAM_CHAT_ID
  CODEX_COMMAND
  COMFYUI_BASE_URL
  COMFYUI_WORKFLOW_PATH
  WORKFLOW_STORAGE_PATH
  PROJECT_ID

Example .env.local:
  TELEGRAM_BOT_TOKEN="..."
  TELEGRAM_CHAT_ID="123456789"
  CODEX_COMMAND="codex"
  COMFYUI_BASE_URL="http://127.0.0.1:8188"
  COMFYUI_WORKFLOW_PATH="./workflow.json"
  WORKFLOW_STORAGE_PATH="./data/projects.json"
  PROJECT_ID="project_xxxxx"
EOF
}

require_token() {
  if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
    echo "Missing TELEGRAM_BOT_TOKEN. Put it in .env.local or export it first." >&2
    exit 1
  fi
}

cmd="${1:-}"

case "$cmd" in
  get-updates)
    require_token
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
    ;;
  import)
    if [[ $# -lt 2 ]]; then
      usage
      exit 1
    fi
    PYTHONPATH=src "${PYTHON_CMD[@]}" -m grok_workflow.cli import "$2"
    ;;
  run)
    require_token
    run_project_id="${2:-$PROJECT_ID}"
    if [[ -z "$run_project_id" ]]; then
      usage
      exit 1
    fi
    if [[ -z "$TELEGRAM_CHAT_ID" ]]; then
      echo "Warning: TELEGRAM_CHAT_ID is empty. Bot can poll commands, but sendMessage notifications will not work." >&2
    fi
    PYTHONPATH=src "${PYTHON_CMD[@]}" -m grok_workflow.cli run-bot "$run_project_id"
    ;;
  *)
    usage
    exit 1
    ;;
esac
