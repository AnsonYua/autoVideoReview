from __future__ import annotations

import json
import shutil
from pathlib import Path

from grok_workflow.adapters.base import ComfyAdapter, GrokAdapter, TelegramGateway
from grok_workflow.config import AppConfig
from grok_workflow.models import (
    Approval,
    ApprovalDecision,
    ProjectStatus,
    ReviewDecision,
    RunnerResult,
    RunnerState,
    Shot,
    ShotContext,
    ShotIteration,
    ShotStatus,
    artifact_path,
    new_id,
    utc_now,
)
from grok_workflow.services.project_ingest import ProjectIngestService
from grok_workflow.storage import Storage


class WorkflowOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        ingest_service: ProjectIngestService,
        grok_adapter: GrokAdapter,
        comfy_adapter: ComfyAdapter,
        telegram_gateway: TelegramGateway,
    ) -> None:
        self.config = config
        self.storage = storage
        self.ingest_service = ingest_service
        self.grok_adapter = grok_adapter
        self.comfy_adapter = comfy_adapter
        self.telegram_gateway = telegram_gateway

    def import_project(self, file_path: str) -> str:
        bundle = self.ingest_service.import_file(file_path)
        self.storage.create_project(bundle.project, bundle.shots)
        self.storage.record_event(bundle.project.id, "project_imported", json.dumps({"source_file": file_path}))
        return bundle.project.id

    def start_project(self, project_id: str) -> None:
        self.storage.update_project_status(project_id, ProjectStatus.RUNNING.value)
        self.telegram_gateway.notify("project_started", {"project_id": project_id})

    def pause_project(self, project_id: str) -> None:
        self.storage.update_project_status(project_id, ProjectStatus.PAUSED.value)
        self.telegram_gateway.notify("project_paused", {"project_id": project_id})

    def resume_project(self, project_id: str) -> None:
        self.storage.update_project_status(project_id, ProjectStatus.RUNNING.value)
        self.telegram_gateway.notify("project_resumed", {"project_id": project_id})

    def get_project_status(self, project_id: str) -> dict[str, object]:
        project = self.storage.get_project(project_id)
        shots = self.storage.list_shots(project_id)
        current = next((shot for shot in shots if shot.status != ShotStatus.APPROVED.value), None)
        return {
            "project_id": project.id,
            "title": project.title,
            "status": project.status,
            "shot_count": len(shots),
            "current_shot_id": current.id if current else None,
            "current_shot_status": current.status if current else None,
        }

    def load_next_runnable_shot(self, project_id: str) -> Shot | None:
        return self.storage.get_next_runnable_shot(project_id)

    def trigger_wan_generation_placeholder(self, project_id: str, shot_id: str) -> RunnerResult:
        return self.generate_shot_video(project_id, shot_id)

    def generate_shot_video(self, project_id: str, shot_id: str) -> RunnerResult:
        shot = self.storage.get_shot(shot_id)
        if shot.project_id != project_id:
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot_id,
                error_code="project_id_mismatch",
                error_message=f"Shot {shot_id} does not belong to {project_id}",
            )
        if shot.status != ShotStatus.PENDING.value:
            return RunnerResult(
                state=RunnerState.IDLE.value,
                project_id=project_id,
                shot_id=shot.id,
                error_code="shot_not_pending",
                error_message=f"Shot {shot.id} status is {shot.status}",
            )

        iteration = ShotIteration(
            id=new_id("iter"),
            shot_id=shot.id,
            iteration_number=self.storage.next_iteration_number(shot.id),
            positive_prompt=shot.positive_prompt,
            negative_prompt=shot.negative_prompt,
        )
        self.storage.create_iteration(iteration)
        payload = {
            "wan_prompt": shot.positive_prompt,
            "negative_prompt": shot.negative_prompt,
            "reference_image_path": shot.reference_image_path,
        }
        submission = self.comfy_adapter.submit_video(payload)
        if submission.status != "ok":
            return self._fail_wan_generation(project_id, shot, iteration, submission)

        iteration.comfy_request_payload = json.dumps(
            {**payload, "comfy_prompt_id": submission.job_id},
            ensure_ascii=False,
        )
        self.storage.update_iteration(iteration)
        self._set_shot_status(shot, ShotStatus.SENT_TO_COMFY)
        self.storage.update_project_status(project_id, ProjectStatus.RUNNING.value)
        self.storage.record_event(
            project_id,
            "wan_generation_started",
            json.dumps({"shot_id": shot.id, "iteration_id": iteration.id, "job_id": submission.job_id}, ensure_ascii=False),
            shot_id=shot.id,
            iteration_id=iteration.id,
        )
        self.telegram_gateway.send_text(
            f"WAN generation started for {shot.id}\n"
            f"status : {ShotStatus.SENT_TO_COMFY.value}"
        )
        return RunnerResult(
            state=RunnerState.RUNNING.value,
            project_id=project_id,
            shot_id=shot.id,
            iteration_id=iteration.id,
        )

    def refresh_wan_generation_status(self, project_id: str, shot_id: str) -> RunnerResult:
        shot = self.storage.get_shot(shot_id)
        if shot.project_id != project_id:
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot_id,
                error_code="project_id_mismatch",
                error_message=f"Shot {shot_id} does not belong to {project_id}",
            )
        if shot.status != ShotStatus.SENT_TO_COMFY.value:
            return RunnerResult(state=RunnerState.IDLE.value, project_id=project_id, shot_id=shot.id)

        iteration = self._latest_iteration_for_shot(shot.id)
        if iteration is None:
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot.id,
                error_code="missing_iteration",
                error_message=f"No iteration found for {shot.id}",
            )

        payload = self._load_iteration_payload(iteration)
        if "wan_prompt" not in payload:
            payload["wan_prompt"] = iteration.positive_prompt or shot.positive_prompt
        if "negative_prompt" not in payload:
            payload["negative_prompt"] = iteration.negative_prompt or shot.negative_prompt
        if "reference_image_path" not in payload:
            payload["reference_image_path"] = shot.reference_image_path
        job_id = str(payload.get("comfy_prompt_id", "")).strip()
        if not job_id:
            job_id = self._recover_active_comfy_prompt_id()
            if not job_id:
                job_id = self._recover_completed_comfy_prompt_id(payload)
            if not job_id:
                return RunnerResult(
                    state=RunnerState.RUNNING.value,
                    project_id=project_id,
                    shot_id=shot.id,
                    iteration_id=iteration.id,
                    error_code="missing_comfy_prompt_id",
                    error_message=f"No ComfyUI prompt id saved for {shot.id}",
                )
            iteration.comfy_request_payload = json.dumps({**payload, "comfy_prompt_id": job_id}, ensure_ascii=False)
            self.storage.update_iteration(iteration)

        generation = self.comfy_adapter.check_generation_result(job_id)
        if generation.status == "running" and self._comfy_queue_is_empty():
            recovered = self.comfy_adapter.recover_latest_output()
            if recovered.status == "ok":
                generation = recovered
        if generation.status == "running":
            return RunnerResult(
                state=RunnerState.RUNNING.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )
        if generation.status != "ok":
            return self._fail_wan_generation(project_id, shot, iteration, generation)

        iteration.output_video_path = generation.video_path
        iteration.output_preview_path = generation.preview_path
        self._save_iteration_artifacts(project_id, shot, iteration)
        self.storage.update_iteration(iteration)
        self._set_shot_status(shot, ShotStatus.VIDEO_GENERATED)
        self.storage.record_event(
            project_id,
            "wan_generation_completed",
            json.dumps(
                {
                    "shot_id": shot.id,
                    "iteration_id": iteration.id,
                    "job_id": generation.job_id,
                    "video_path": generation.video_path,
                    "preview_path": generation.preview_path,
                },
                ensure_ascii=False,
            ),
            shot_id=shot.id,
            iteration_id=iteration.id,
        )
        review_result = self.review_generated_iteration(project_id, shot, iteration)
        if review_result.state != RunnerState.IDLE.value:
            return review_result
        self.telegram_gateway.send_text(
            f"WAN generation completed for {shot.id}\n"
            f"video_path : {generation.video_path}\n"
            f"preview_path : {generation.preview_path}\n"
            f"status : {ShotStatus.VIDEO_GENERATED.value}"
        )
        return RunnerResult(
            state=RunnerState.IDLE.value,
            project_id=project_id,
            shot_id=shot.id,
            iteration_id=iteration.id,
        )

    def review_generated_iteration(self, project_id: str, shot: Shot, iteration: ShotIteration) -> RunnerResult:
        if not iteration.output_video_path or not Path(iteration.output_video_path).exists():
            self._refresh_iteration_artifacts_from_comfy(iteration)
            self._save_iteration_artifacts(project_id, shot, iteration)
            self.storage.update_iteration(iteration)
        if not iteration.output_video_path or not Path(iteration.output_video_path).exists():
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
                error_code="missing_video_path",
                error_message=f"No generated video path saved for {iteration.id}",
            )

        self._set_shot_status(shot, ShotStatus.SENT_TO_GROK_REVIEW)
        self.telegram_gateway.send_text(f"Grok review started for {shot.id}")
        context = self._build_shot_context(project_id, shot)
        review = self.grok_adapter.review_video(context, iteration, iteration.output_video_path)
        iteration.grok_review_raw = review.raw_text
        iteration.grok_review_status = review.review_result
        iteration.grok_revision_notes = review.fix_notes or review.reason
        self.storage.update_iteration(iteration)

        if review.status != "ok":
            self._set_shot_status(shot, ShotStatus.SENT_TO_GROK_REVIEW)
            self.telegram_gateway.send_text(
                f"Grok review failed for {shot.id}\n"
                f"error_code : {review.error_code}\n"
                f"error_message : {review.error_message}"
            )
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
                error_code=review.error_code,
                error_message=review.error_message,
            )

        if review.review_result == ReviewDecision.FAIL.value:
            shot.positive_prompt = review.updated_wan_prompt or shot.positive_prompt
            shot.negative_prompt = review.updated_negative_prompt or shot.negative_prompt
            self._set_shot_status(shot, ShotStatus.PENDING)
            self.storage.record_event(
                project_id,
                "grok_review_failed_retry",
                json.dumps(
                    {
                        "shot_id": shot.id,
                        "iteration_id": iteration.id,
                        "raw_text": review.raw_text,
                    },
                    ensure_ascii=False,
                ),
                shot_id=shot.id,
                iteration_id=iteration.id,
            )
            self.telegram_gateway.send_text(
                f"Grok review failed for {shot.id}\n"
                "Improved prompts saved. Run /check_status and click the shot again."
            )
            return RunnerResult(
                state=RunnerState.RETRY.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )

        self._set_shot_status(shot, ShotStatus.GROK_PASSED_WAITING_USER)
        self.storage.update_project_status(project_id, ProjectStatus.WAITING_APPROVAL.value)
        self.storage.save_approval(Approval(shot_id=shot.id, iteration_id=iteration.id, grok_passed_at=utc_now()))
        caption = (
            f"Grok review PASS for {shot.id}\n"
            f"Reply /approve {shot.id} {iteration.id} or /reject {shot.id} {iteration.id}"
        )
        self.telegram_gateway.send_video(iteration.output_video_path, caption)
        self.telegram_gateway.request_approval(shot.id, iteration.id, iteration.output_preview_path)
        return RunnerResult(
            state=RunnerState.WAITING_APPROVAL.value,
            project_id=project_id,
            shot_id=shot.id,
            iteration_id=iteration.id,
        )

    def review_generated_shot(self, project_id: str, shot_id: str) -> RunnerResult:
        shot = self.storage.get_shot(shot_id)
        if shot.project_id != project_id:
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot_id,
                error_code="project_id_mismatch",
                error_message=f"Shot {shot_id} does not belong to {project_id}",
            )
        if shot.status not in {ShotStatus.VIDEO_GENERATED.value, ShotStatus.SENT_TO_GROK_REVIEW.value}:
            return RunnerResult(state=RunnerState.IDLE.value, project_id=project_id, shot_id=shot.id)
        iteration = self._latest_iteration_for_shot(shot.id)
        if iteration is None:
            return RunnerResult(
                state=RunnerState.ERROR.value,
                project_id=project_id,
                shot_id=shot.id,
                error_code="missing_iteration",
                error_message=f"No iteration found for {shot.id}",
            )
        return self.review_generated_iteration(project_id, shot, iteration)

    def _fail_wan_generation(
        self,
        project_id: str,
        shot: Shot,
        iteration: ShotIteration,
        generation: RunnerResult | object,
    ) -> RunnerResult:
        error_code = str(getattr(generation, "error_code", "wan_generation_failed"))
        error_message = str(getattr(generation, "error_message", "WAN generation failed"))
        self._set_shot_status(shot, ShotStatus.ERROR)
        self.storage.update_project_status(project_id, ProjectStatus.ERROR.value)
        self.storage.record_event(
            project_id,
            "wan_generation_failed",
            json.dumps(
                {
                    "shot_id": shot.id,
                    "iteration_id": iteration.id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
                ensure_ascii=False,
            ),
            shot_id=shot.id,
            iteration_id=iteration.id,
        )
        self.telegram_gateway.send_text(
            f"WAN generation failed for {shot.id}\n"
            f"error_code : {error_code}\n"
            f"error_message : {error_message}\n"
            f"status : {ShotStatus.ERROR.value}"
        )
        return RunnerResult(
            state=RunnerState.ERROR.value,
            project_id=project_id,
            shot_id=shot.id,
            iteration_id=iteration.id,
            error_code=error_code,
            error_message=error_message,
        )

    def _latest_iteration_for_shot(self, shot_id: str) -> ShotIteration | None:
        iterations = self.storage.list_iterations(shot_id)
        return iterations[-1] if iterations else None

    def _load_iteration_payload(self, iteration: ShotIteration) -> dict[str, object]:
        if not iteration.comfy_request_payload:
            return {}
        try:
            payload = json.loads(iteration.comfy_request_payload)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_iteration_artifacts(self, project_id: str, shot: Shot, iteration: ShotIteration) -> None:
        target_dir = self._ensure_artifact_dirs(project_id, shot.shot_number, iteration.iteration_number)
        if iteration.output_video_path:
            saved_video = self._copy_artifact(iteration.output_video_path, target_dir)
            if saved_video:
                iteration.output_video_path = str(saved_video)
        if iteration.output_preview_path:
            saved_preview = self._copy_artifact(iteration.output_preview_path, target_dir)
            if saved_preview:
                iteration.output_preview_path = str(saved_preview)

    def _refresh_iteration_artifacts_from_comfy(self, iteration: ShotIteration) -> None:
        payload = self._load_iteration_payload(iteration)
        job_id = str(payload.get("comfy_prompt_id", "")).strip()
        generation = self.comfy_adapter.check_generation_result(job_id) if job_id else self.comfy_adapter.recover_latest_output()
        if generation.status != "ok" and self._comfy_queue_is_empty():
            generation = self.comfy_adapter.recover_latest_output()
        if generation.status != "ok":
            return
        iteration.output_video_path = generation.video_path
        iteration.output_preview_path = generation.preview_path

    def _copy_artifact(self, source_path: str, target_dir: Path) -> Path | None:
        source = Path(source_path)
        if not source.exists():
            return None
        target = target_dir / self._safe_artifact_name(source.name)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    def _safe_artifact_name(self, filename: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        sanitized = "".join("_" if char in invalid_chars else char for char in filename)
        return sanitized.strip().strip(".") or "artifact"

    def _recover_active_comfy_prompt_id(self) -> str:
        finder = getattr(self.comfy_adapter, "find_active_prompt_id", None)
        if finder is None:
            return ""
        prompt_id = finder()
        return str(prompt_id).strip()

    def _recover_completed_comfy_prompt_id(self, payload: dict[str, object]) -> str:
        finder = getattr(self.comfy_adapter, "find_completed_prompt_id", None)
        if finder is None:
            return ""
        prompt_id = finder(payload)
        return str(prompt_id).strip()

    def _comfy_queue_is_empty(self) -> bool:
        getter = getattr(self.comfy_adapter, "_get_json", None)
        if getter is None:
            return False
        result = getter("/queue")
        if result.get("status") == "error":
            return False
        data = result.get("data", {})
        if not isinstance(data, dict):
            return False
        return not data.get("queue_running") and not data.get("queue_pending")

    def complete_project(self, project_id: str) -> None:
        self.storage.update_project_status(project_id, ProjectStatus.COMPLETED.value)
        self.telegram_gateway.notify("project_completed", {"project_id": project_id})

    def execute_shot(self, project_id: str, shot: Shot) -> RunnerResult:
        context = self._build_shot_context(project_id, shot)
        iteration = self._generate_iteration_for_shot(project_id, shot, context)
        if iteration is None:
            return RunnerResult(
                state=RunnerState.PAUSED.value,
                project_id=project_id,
                shot_id=shot.id,
            )

        payload = self._build_generation_payload(iteration, context)
        generation = self._submit_generation(project_id, shot, iteration, payload)
        if generation is None:
            return RunnerResult(
                state=RunnerState.PAUSED.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )

        review = self._review_generation(project_id, shot, iteration, context, generation.video_path)
        if review is None:
            return RunnerResult(
                state=RunnerState.PAUSED.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )

        return self._apply_review_outcome(project_id, shot, iteration, review)

    def apply_approval_decision(self, project_id: str, shot_id: str, iteration_id: str, event_type: str) -> RunnerResult:
        shot = self.storage.get_shot(shot_id)
        iteration = self.storage.get_iteration(iteration_id)

        if event_type == ApprovalDecision.APPROVE.value:
            shot.approved_iteration_id = iteration.id
            self._set_shot_status(shot, ShotStatus.APPROVED)
            self.storage.save_approval(
                Approval(
                    shot_id=shot.id,
                    iteration_id=iteration.id,
                    grok_passed_at=utc_now(),
                    user_decision=ApprovalDecision.APPROVE.value,
                    user_decision_at=utc_now(),
                )
            )
            self.telegram_gateway.notify("shot_approved", {"shot_id": shot.id, "iteration_id": iteration.id})
            self.storage.update_project_status(shot.project_id, ProjectStatus.RUNNING.value)
            return RunnerResult(
                state=RunnerState.RUNNING.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )

        if event_type == ApprovalDecision.REJECT.value:
            shot.positive_prompt = iteration.positive_prompt
            shot.negative_prompt = iteration.negative_prompt
            self._set_shot_status(shot, ShotStatus.REJECTED)
            self.storage.save_approval(
                Approval(
                    shot_id=shot.id,
                    iteration_id=iteration.id,
                    grok_passed_at=utc_now(),
                    user_decision=ApprovalDecision.REJECT.value,
                    user_decision_at=utc_now(),
                )
            )
            self.telegram_gateway.notify("shot_rejected", {"shot_id": shot.id, "iteration_id": iteration.id})
            shot.status = ShotStatus.PENDING.value
            self.storage.update_shot(shot)
            self.storage.update_project_status(shot.project_id, ProjectStatus.RUNNING.value)
            return RunnerResult(
                state=RunnerState.RUNNING.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )

        raise ValueError(f"Unsupported control event {event_type}")

    def _build_shot_context(self, project_id: str, shot: Shot) -> ShotContext:
        project = self.storage.get_project(project_id)
        previous_shot = self.storage.get_previous_approved_shot(project_id, shot.shot_number)
        previous_iteration = self.storage.get_selected_iteration(previous_shot) if previous_shot else None
        resolved_reference_image_path = shot.reference_image_path or (
            previous_iteration.output_preview_path if previous_iteration else ""
        )
        return ShotContext(
            project=project,
            shot=shot,
            previous_shot=previous_shot,
            previous_iteration=previous_iteration,
            resolved_reference_image_path=resolved_reference_image_path,
        )

    def _generate_iteration_for_shot(self, project_id: str, shot: Shot, context: ShotContext) -> ShotIteration | None:
        self._set_shot_status(shot, ShotStatus.SENT_TO_GROK)
        self.telegram_gateway.notify("shot_started", {"project_id": project_id, "shot_id": shot.id})

        prompt_result = self.grok_adapter.generate_prompt(context)
        if prompt_result.status != "ok":
            self._pause_on_error(project_id, shot, None, prompt_result.error_code, prompt_result.error_message)
            return None

        self._set_shot_status(shot, ShotStatus.GROK_PROMPT_RECEIVED)
        iteration = ShotIteration(
            id=new_id("iter"),
            shot_id=shot.id,
            iteration_number=self.storage.next_iteration_number(shot.id),
            positive_prompt=prompt_result.wan_prompt,
            negative_prompt=prompt_result.negative_prompt,
            motion_notes=prompt_result.motion_notes,
        )
        self.storage.create_iteration(iteration)
        return iteration

    def _build_generation_payload(self, iteration: ShotIteration, context: ShotContext) -> dict[str, object]:
        return {
            "wan_prompt": iteration.positive_prompt,
            "negative_prompt": iteration.negative_prompt,
            "motion_notes": iteration.motion_notes,
            "previous_video_path": context.previous_iteration.output_video_path if context.previous_iteration else "",
            "reference_image_path": context.resolved_reference_image_path,
        }

    def _submit_generation(self, project_id: str, shot: Shot, iteration: ShotIteration, payload: dict[str, object]):
        iteration.comfy_request_payload = json.dumps(payload, ensure_ascii=False)
        self.storage.update_iteration(iteration)
        self._set_shot_status(shot, ShotStatus.SENT_TO_COMFY)

        generation = self.comfy_adapter.generate_video(payload)
        if generation.status != "ok":
            self._pause_on_error(project_id, shot, iteration, generation.error_code, generation.error_message)
            return None

        iteration.output_video_path = generation.video_path
        iteration.output_preview_path = generation.preview_path
        self.storage.update_iteration(iteration)
        self._ensure_artifact_dirs(project_id, shot.shot_number, iteration.iteration_number)
        self._set_shot_status(shot, ShotStatus.SENT_TO_GROK_REVIEW)
        self.telegram_gateway.notify("video_generated", {"shot_id": shot.id, "iteration_id": iteration.id})
        return generation

    def _review_generation(self, project_id: str, shot: Shot, iteration: ShotIteration, context: ShotContext, video_path: str):
        review = self.grok_adapter.review_video(context, iteration, video_path)
        if review.status != "ok":
            self._pause_on_error(project_id, shot, iteration, review.error_code, review.error_message)
            return None
        iteration.grok_review_raw = review.raw_text
        iteration.grok_review_status = review.review_result
        iteration.grok_revision_notes = review.fix_notes
        self.storage.update_iteration(iteration)
        return review

    def _apply_review_outcome(self, project_id: str, shot: Shot, iteration: ShotIteration, review) -> RunnerResult:
        if review.review_result == ReviewDecision.FAIL.value:
            shot.positive_prompt = review.updated_wan_prompt or shot.positive_prompt
            self._set_shot_status(shot, ShotStatus.GROK_FAILED)
            self.telegram_gateway.notify(
                "grok_failed_autoregenerate",
                {"shot_id": shot.id, "iteration_id": iteration.id, "reason": review.reason},
            )
            shot.status = ShotStatus.PENDING.value
            self.storage.update_shot(shot)
            return RunnerResult(
                state=RunnerState.RETRY.value,
                project_id=project_id,
                shot_id=shot.id,
                iteration_id=iteration.id,
            )

        self._set_shot_status(shot, ShotStatus.GROK_PASSED_WAITING_USER)
        self.storage.update_project_status(project_id, ProjectStatus.WAITING_APPROVAL.value)
        self.storage.save_approval(Approval(shot_id=shot.id, iteration_id=iteration.id, grok_passed_at=utc_now()))
        self.telegram_gateway.request_approval(shot.id, iteration.id, iteration.output_preview_path)
        return RunnerResult(
            state=RunnerState.WAITING_APPROVAL.value,
            project_id=project_id,
            shot_id=shot.id,
            iteration_id=iteration.id,
        )

    def _ensure_artifact_dirs(self, project_id: str, shot_number: int, iteration_number: int) -> Path:
        path = artifact_path(self.config.data_dir, project_id, shot_number, iteration_number)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pause_on_error(self, project_id: str, shot: Shot, iteration: ShotIteration | None, error_code: str, error_message: str) -> Shot:
        self._set_shot_status(shot, ShotStatus.PAUSED)
        self.storage.update_project_status(project_id, ProjectStatus.PAUSED.value)
        self.telegram_gateway.notify(
            "workflow_paused",
            {
                "project_id": project_id,
                "shot_id": shot.id,
                "iteration_id": iteration.id if iteration else None,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        return shot

    def _set_shot_status(self, shot: Shot, status: ShotStatus) -> None:
        shot.status = status.value
        self.storage.update_shot(shot)
