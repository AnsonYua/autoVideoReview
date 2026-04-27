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

    @abstractmethod
    def consume_command(self) -> ControlEvent | None:
        raise NotImplementedError
