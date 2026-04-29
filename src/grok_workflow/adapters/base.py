from __future__ import annotations

from abc import ABC, abstractmethod

from grok_workflow.models import (
    ControlEvent,
    GenerationResult,
    JobStatusResult,
    OutputArtifacts,
    ReviewResult,
    ShotIteration,
    ShotContext,
    StructuredPromptResult,
)


class GrokAdapter(ABC):
    @abstractmethod
    def generate_prompt(self, shot_context: ShotContext) -> StructuredPromptResult:
        raise NotImplementedError

    @abstractmethod
    def review_video(self, shot_context: ShotContext, iteration: ShotIteration, video_path: str) -> ReviewResult:
        raise NotImplementedError


class ComfyAdapter(ABC):
    def submit_video(self, prompt_payload: dict[str, object]) -> GenerationResult:
        return self.generate_video(prompt_payload)

    def check_generation_result(self, job_id: str) -> GenerationResult:
        status = self.get_job_status(job_id)
        if status.status == "error":
            return GenerationResult(
                status="error",
                job_id=job_id,
                error_code=status.error_code,
                error_message=status.error_message,
                raw_response=status.raw_response,
            )
        if status.state != "completed":
            return GenerationResult(status="running", job_id=job_id, raw_response=status.raw_response)
        artifacts = self.collect_outputs(job_id)
        return GenerationResult(
            status=artifacts.status,
            job_id=job_id,
            video_path=artifacts.video_path,
            preview_path=artifacts.preview_path,
            raw_response=artifacts.raw_response,
            error_code=artifacts.error_code,
            error_message=artifacts.error_message,
        )

    def recover_latest_output(self) -> GenerationResult:
        return GenerationResult(
            status="error",
            error_code="recover_output_not_supported",
            error_message="This Comfy adapter cannot recover latest output files.",
        )

    @abstractmethod
    def generate_video(self, prompt_payload: dict[str, object]) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    def get_job_status(self, job_id: str) -> JobStatusResult:
        raise NotImplementedError

    @abstractmethod
    def collect_outputs(self, job_id: str) -> OutputArtifacts:
        raise NotImplementedError


class TelegramGateway(ABC):
    @abstractmethod
    def notify(self, event_type: str, payload: dict[str, object]) -> None:
        raise NotImplementedError

    @abstractmethod
    def request_approval(self, shot_id: str, iteration_id: str, preview_path: str) -> None:
        raise NotImplementedError

    def send_video(self, video_path: str, caption: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def consume_command(self) -> ControlEvent | None:
        raise NotImplementedError
