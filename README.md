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
COMFYUI_WORKFLOW_PATH="./workflow.json"
WORKFLOW_STORAGE_PATH="./data/projects.json"
PROJECT_ID="project_xxxxx"
```

`PROJECT_ID` is required. The runner uses the project already stored in `WORKFLOW_STORAGE_PATH` and listens for Telegram commands for that project.

Supported Telegram commands:

```text
/menu
/help
check status
start project
pause project
resume project
/status PROJECT_ID
/start_project PROJECT_ID
/pause PROJECT_ID
/resume PROJECT_ID
/approve SHOT_ID ITERATION_ID
/reject SHOT_ID ITERATION_ID
```
