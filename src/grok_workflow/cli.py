from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path

from grok_workflow.adapters.comfy_api import ComfyUIApiAdapter
from grok_workflow.adapters.grok_cli import CodexCliGrokAdapter
from grok_workflow.adapters.telegram import TelegramBotGateway
from grok_workflow.config import AppConfig
from grok_workflow.services.orchestrator import WorkflowOrchestrator
from grok_workflow.services.project_ingest import ProjectIngestService
from grok_workflow.services.telegram_command_processor import TelegramCommandProcessor
from grok_workflow.services.workflow_runner import WorkflowRunner
from grok_workflow.storage import Storage


def load_env_file(path: Path = Path(".env.local")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def load_config() -> AppConfig:
    config = AppConfig()
    config.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if poll_timeout := os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS"):
        config.telegram.poll_timeout_seconds = int(poll_timeout)
    if codex_cmd := os.getenv("CODEX_COMMAND"):
        config.grok_cli.command = shlex.split(codex_cmd)
    if codex_timeout := os.getenv("CODEX_TIMEOUT_SECONDS"):
        config.grok_cli.timeout_seconds = int(codex_timeout)
    if comfy_base_url := os.getenv("COMFYUI_BASE_URL"):
        config.comfyui.base_url = comfy_base_url
    if workflow_path := os.getenv("COMFYUI_WORKFLOW_PATH"):
        config.comfyui.workflow_template_path = config.comfyui.workflow_template_path.__class__(workflow_path)
    if storage_path := os.getenv("WORKFLOW_STORAGE_PATH"):
        config.storage_path = config.storage_path.__class__(storage_path)
    return config


def build_orchestrator(config: AppConfig) -> WorkflowOrchestrator:
    storage = Storage(config.storage_path)
    ingest_service = ProjectIngestService()
    grok_adapter = CodexCliGrokAdapter(config.grok_cli)
    comfy_adapter = ComfyUIApiAdapter(config.comfyui)
    telegram_gateway = TelegramBotGateway(config.telegram)
    return WorkflowOrchestrator(
        config=config,
        storage=storage,
        ingest_service=ingest_service,
        grok_adapter=grok_adapter,
        comfy_adapter=comfy_adapter,
        telegram_gateway=telegram_gateway,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Grok workflow Telegram bot.")
    parser.add_argument("--idle-sleep", type=float, default=1.0, help="Seconds to sleep between empty polls.")
    args = parser.parse_args()

    load_env_file()
    project_id = os.getenv("PROJECT_ID", "").strip()
    config = load_config()
    errors = []
    if not config.telegram.bot_token:
        errors.append("Missing TELEGRAM_BOT_TOKEN. Put it in .env.local or set it before running.")
    if not project_id:
        errors.append("Missing PROJECT_ID. Put it in .env.local or set it before running.")
    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        raise SystemExit(1)

    orchestrator = build_orchestrator(config)
    print(f"Telegram bot polling started for {project_id}. Press Ctrl+C to stop.", flush=True)
    run_bot_loop(orchestrator, project_id, args.idle_sleep)


def run_bot_loop(orchestrator: WorkflowOrchestrator, project_id: str, idle_sleep: float) -> None:
    runner = WorkflowRunner(orchestrator)
    command_processor = TelegramCommandProcessor(orchestrator, runner, project_id)
    orchestrator.telegram_gateway.send_text("computer is ready")
    while True:
        if not command_processor.process_next_command():
            time.sleep(idle_sleep)


if __name__ == "__main__":
    main()
