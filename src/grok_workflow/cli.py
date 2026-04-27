from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from dataclasses import asdict
from pathlib import Path

from grok_workflow.adapters.comfy_api import ComfyUIApiAdapter
from grok_workflow.adapters.grok_cli import CodexCliGrokAdapter
from grok_workflow.adapters.telegram import TelegramBotGateway
from grok_workflow.config import AppConfig
from grok_workflow.models import ShotContext, ShotIteration
from grok_workflow.services.orchestrator import WorkflowOrchestrator
from grok_workflow.services.project_ingest import ProjectIngestService
from grok_workflow.services.telegram_command_processor import TelegramCommandProcessor
from grok_workflow.services.workflow_runner import WorkflowRunner
from grok_workflow.storage import Storage


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
    parser = argparse.ArgumentParser(description="Grok workflow orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("file_path")

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("project_id")

    run_parser = subparsers.add_parser("run-bot")
    run_parser.add_argument("project_id")
    run_parser.add_argument("--idle-sleep", type=float, default=1.0)

    review_parser = subparsers.add_parser("review-grok")
    review_parser.add_argument("project_id")
    review_parser.add_argument("shot_id")
    review_parser.add_argument("iteration_id")
    review_parser.add_argument("--video-path")
    review_parser.add_argument("--positive-prompt")
    review_parser.add_argument("--negative-prompt", default="")
    review_parser.add_argument("--motion-notes", default="")

    args = parser.parse_args()
    orchestrator = build_orchestrator(load_config())

    if args.command == "import":
        project_id = orchestrator.import_project(args.file_path)
        print(project_id)
        return
    if args.command == "start":
        orchestrator.start_project(args.project_id)
        runner = WorkflowRunner(orchestrator)
        runner.run_until_blocked(args.project_id)
        return
    if args.command == "run-bot":
        run_bot_loop(orchestrator, args.project_id, args.idle_sleep)
        return
    if args.command == "review-grok":
        review_grok(
            orchestrator,
            args.project_id,
            args.shot_id,
            args.iteration_id,
            args.video_path,
            args.positive_prompt,
            args.negative_prompt,
            args.motion_notes,
        )
        return


def run_bot_loop(orchestrator: WorkflowOrchestrator, project_id: str, idle_sleep: float) -> None:
    runner = WorkflowRunner(orchestrator)
    command_processor = TelegramCommandProcessor(orchestrator, runner, project_id)
    orchestrator.telegram_gateway.notify("worker_started", {"project_id": project_id})
    while True:
        if not command_processor.process_next_command():
            time.sleep(idle_sleep)


def review_grok(
    orchestrator: WorkflowOrchestrator,
    project_id: str,
    shot_id: str,
    iteration_id: str,
    video_path: str | None,
    positive_prompt: str | None,
    negative_prompt: str,
    motion_notes: str,
) -> None:
    storage = orchestrator.storage
    project = storage.get_project(project_id)
    shot = storage.get_shot(shot_id)
    if shot.project_id != project.id:
        raise ValueError(f"Shot {shot_id} does not belong to project {project_id}")

    context = ShotContext(project=project, shot=shot)

    try:
        iteration = storage.get_iteration(iteration_id)
        resolved_video_path = video_path or iteration.output_video_path
    except KeyError:
        iteration = ShotIteration(
            id=iteration_id,
            shot_id=shot_id,
            iteration_number=0,
            positive_prompt=positive_prompt or "",
            negative_prompt=negative_prompt,
            motion_notes=motion_notes,
        )
        resolved_video_path = video_path or ""

    if not resolved_video_path:
        raise ValueError(
            "No video path available. Pass --video-path for dev review or use a real iteration with output_video_path."
        )
    if not iteration.positive_prompt:
        raise ValueError(
            "No iteration positive prompt available. Pass --positive-prompt for dev review or use a real iteration."
        )

    resolved_video_path = str(Path(resolved_video_path).expanduser().resolve())
    if not Path(resolved_video_path).exists():
        raise FileNotFoundError(f"Video file not found: {resolved_video_path}")

    review = orchestrator.grok_adapter.review_video(context, iteration, resolved_video_path)
    print(
        json.dumps(
            {
                "project_id": project_id,
                "shot_id": shot_id,
                "iteration_id": iteration.id,
                "video_path": resolved_video_path,
                "positive_prompt": iteration.positive_prompt,
                "negative_prompt": iteration.negative_prompt,
                "motion_notes": iteration.motion_notes,
                "review": asdict(review),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
