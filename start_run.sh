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

PYTHONPATH=src "${PYTHON_CMD[@]}" -m grok_workflow.cli
