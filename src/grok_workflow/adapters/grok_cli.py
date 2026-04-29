from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
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
        if self.config.review_first_landing:
            return self._review_video_with_playwright(iteration, video_path)
        payload = self._review_payload(shot_context, iteration, video_path)
        result = self._run_cli(payload)
        return ReviewResult(**self._normalize_review_result(result))

    def _review_video_with_playwright(self, iteration: ShotIteration, video_path: str) -> ReviewResult:
        if not self._chrome_debug_reachable():
            return ReviewResult(
                status="error",
                review_result="FAIL",
                error_code="chrome_debug_unreachable",
                error_message=(
                    f"Chrome DevTools is not reachable at {self.config.review_cdp_url}. "
                    "Start Chrome with --remote-debugging-port=9222, then run /check_status again."
                ),
            )
        command = [
            sys.executable,
            str(self.config.review_script_path),
            "--connect-existing",
            "--cdp-url",
            self.config.review_cdp_url,
            "--first-landing",
            self.config.review_first_landing,
            "--video-path",
            video_path,
            "--positive-prompt",
            iteration.positive_prompt,
            "--negative-prompt",
            iteration.negative_prompt,
            "--send-review-prompt",
            "--submit-review-prompt",
            "--read-review-result",
            "--timeout-ms",
            str(self.config.review_timeout_ms),
            "--result-timeout-ms",
            str(self.config.review_result_timeout_ms),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.config.working_directory,
                capture_output=True,
                text=True,
                timeout=max(self.config.timeout_seconds, self.config.review_result_timeout_ms // 1000 + 60),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ReviewResult(
                status="error",
                review_result="FAIL",
                error_code="grok_review_failed",
                error_message=str(exc),
                raw_text="",
            )
        output = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
        if completed.returncode != 0 and not output:
            return ReviewResult(
                status="error",
                review_result="FAIL",
                error_code="grok_review_failed",
                error_message=completed.stderr.strip() or "Grok review script failed",
                raw_text=completed.stdout.strip(),
            )
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            return ReviewResult(
                status="error",
                review_result="FAIL",
                error_code="invalid_review_json",
                error_message=f"Grok review script returned invalid JSON: {exc}",
                raw_text=completed.stdout,
            )
        return ReviewResult(**self._normalize_review_result(result))

    def _chrome_debug_reachable(self) -> bool:
        probe_url = self.config.review_cdp_url.rstrip("/") + "/json/version"
        try:
            with urllib.request.urlopen(probe_url, timeout=3):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def _run_cli(self, payload: dict[str, object]) -> dict[str, object]:
        command = [*self.config.command, json.dumps(payload)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.config.working_directory,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "error",
                "error_code": "codex_cli_failed",
                "error_message": str(exc),
                "raw_text": "",
            }
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
