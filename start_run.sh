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
export COMFYUI_WORKFLOW_PATH="${COMFYUI_WORKFLOW_PATH:-$ROOT_DIR/sample_data/wan22-remix-face-lock-autosave-lastframe-clip4.json}"
export COMFYUI_OUTPUT_DIR="${COMFYUI_OUTPUT_DIR:-$ROOT_DIR/outputs}"
export GROK_REVIEW_SCRIPT_PATH="${GROK_REVIEW_SCRIPT_PATH:-$ROOT_DIR/tools/playwright_grok_review.py}"
export GROK_REVIEW_FIRST_LANDING="${GROK_REVIEW_FIRST_LANDING:-https://grok.com/project/66b6fdb6-3ae4-4909-b421-59f7fc56ef09?chat=6c93a5e3-373d-4f12-b0df-7f2e588d2016&rid=40c486af-6668-4fbb-8d22-b9759e67ffc1}"
export GROK_REVIEW_CDP_URL="${GROK_REVIEW_CDP_URL:-http://127.0.0.1:9222}"
export WORKFLOW_STORAGE_PATH="${WORKFLOW_STORAGE_PATH:-$ROOT_DIR/data/projects.json}"
export PROJECT_ID="${PROJECT_ID:-project_c022d8962864}"

PYTHONPATH=src "${PYTHON_CMD[@]}" -m grok_workflow.cli
