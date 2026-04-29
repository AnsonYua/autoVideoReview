# Grok Workflow Runner

This repo now has one runtime entrypoint. Configure the project and services in `.env.local`, then start the Telegram polling bot.

Windows:

```bat
start_run.bat
```

macOS/Linux:

```bash
./start_run.sh
```

Direct Python entrypoint:

```bash
python -m grok_workflow.cli
```

## Configuration

Create `.env.local` from `.env.local.example` and set the values for your machine:

```text
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="123456789"
CODEX_COMMAND="codex"
COMFYUI_BASE_URL="http://127.0.0.1:8188"
COMFYUI_WORKFLOW_PATH="./sample_data/wan22-remix-face-lock-autosave-lastframe-clip4.json"
COMFYUI_OUTPUT_DIR="./outputs"
GROK_REVIEW_SCRIPT_PATH="./tools/playwright_grok_review.py"
GROK_REVIEW_FIRST_LANDING="https://grok.com/project/66b6fdb6-3ae4-4909-b421-59f7fc56ef09?chat=6c93a5e3-373d-4f12-b0df-7f2e588d2016&rid=40c486af-6668-4fbb-8d22-b9759e67ffc1"
GROK_REVIEW_CDP_URL="http://127.0.0.1:9222"
WORKFLOW_STORAGE_PATH="./data/projects.json"
PROJECT_ID="project_xxxxx"
```

`PROJECT_ID` is required. The runner uses the project already stored in `WORKFLOW_STORAGE_PATH` and listens for Telegram commands for that project.

Supported Telegram commands:

```text
/menu
/help
/check_status
/shot_SHOT_ID
/approve SHOT_ID ITERATION_ID
/reject SHOT_ID ITERATION_ID
```
