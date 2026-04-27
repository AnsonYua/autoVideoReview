from __future__ import annotations

import json
import subprocess
from dataclasses import asdict

from grok_workflow.adapters.base import GrokAdapter
from grok_workflow.config import GrokCliConfig
from grok_workflow.models import ReviewResult, ShotContext, ShotIteration, StructuredPromptResult


class CodexCliGrokAdapter(GrokAdapter):
    def __init__(self, config: GrokCliConfig) -> None:
        self.config = config

    def generate_prompt(self, shot_context: ShotContext) -> StructuredPromptResult:
        payload = {
            "task": "generate_prompt",
            "shot_context": self._shot_context_payload(shot_context),
        }
        result = self._run_cli(payload)
        return StructuredPromptResult(**result)

    def review_video(self, shot_context: ShotContext, iteration: ShotIteration, video_path: str) -> ReviewResult:
        payload = self._review_payload(shot_context, iteration, video_path)
        result = self._run_cli(payload)
        return ReviewResult(**self._normalize_review_result(result))

    def _run_cli(self, payload: dict[str, object]) -> dict[str, object]:
        command = [*self.config.command, json.dumps(payload)]
        completed = subprocess.run(
            command,
            cwd=self.config.working_directory,
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "status": "error",
                "error_code": "codex_cli_failed",
                "error_message": completed.stderr.strip() or completed.stdout.strip() or "Codex CLI call failed",
                "raw_text": completed.stdout.strip(),
            }
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return {
                "status": "error",
                "error_code": "invalid_json",
                "error_message": f"Codex CLI returned invalid JSON: {exc}",
                "raw_text": completed.stdout,
            }

    def _shot_context_payload(self, shot_context: ShotContext) -> dict[str, object]:
        payload = {
            "project": asdict(shot_context.project),
            "shot": asdict(shot_context.shot),
        }
        if shot_context.previous_shot:
            payload["previous_shot"] = asdict(shot_context.previous_shot)
        if shot_context.previous_iteration:
            payload["previous_iteration"] = asdict(shot_context.previous_iteration)
        return payload

    def _review_payload(self, shot_context: ShotContext, iteration: ShotIteration, video_path: str) -> dict[str, object]:
        return {
            "task": "review_video",
            "video_path": video_path,
            "review_context": {
                "project_id": shot_context.project.id,
                "project_title": shot_context.project.title,
                "shot_id": shot_context.shot.id,
                "shot_number": shot_context.shot.shot_number,
                "iteration_id": iteration.id,
                "iteration_number": iteration.iteration_number,
                "positive_prompt": iteration.positive_prompt,
                "negative_prompt": iteration.negative_prompt,
                "motion_notes": iteration.motion_notes,
            },
        }

    def _normalize_review_result(self, result: dict[str, object]) -> dict[str, object]:
        if "review_result" in result:
            return result

        if "pass_or_fail" in result:
            status = str(result.get("status", "error"))
            pass_or_fail = str(result.get("pass_or_fail", "FAIL"))
            improved_positive_prompt = str(result.get("improved_positive_prompt", ""))
            improved_negative_prompt = str(result.get("improved_negative_prompt", ""))
            raw_text = str(result.get("raw_text", ""))
            return {
                "status": status,
                "review_result": pass_or_fail,
                "reason": "" if status == "ok" else raw_text,
                "fix_notes": improved_negative_prompt,
                "updated_wan_prompt": improved_positive_prompt,
                "updated_negative_prompt": improved_negative_prompt,
                "raw_text": raw_text,
                "error_code": "" if status == "ok" else "grok_review_failed",
                "error_message": "" if status == "ok" else raw_text,
            }

        return {
            "status": "error",
            "review_result": "FAIL",
            "reason": "",
            "fix_notes": "",
            "updated_wan_prompt": "",
            "updated_negative_prompt": "",
            "raw_text": json.dumps(result, ensure_ascii=False),
            "error_code": "invalid_review_shape",
            "error_message": "Review output did not match the expected schema",
        }
