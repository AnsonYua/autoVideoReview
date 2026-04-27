from __future__ import annotations

import json
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
