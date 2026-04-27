from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ShotStatus(StrEnum):
    PENDING = "pending"
    DRAFT_PROMPT_READY = "draft_prompt_ready"
    SENT_TO_GROK = "sent_to_grok"
    GROK_PROMPT_RECEIVED = "grok_prompt_received"
    SENT_TO_COMFY = "sent_to_comfy"
    VIDEO_GENERATED = "video_generated"
    SENT_TO_GROK_REVIEW = "sent_to_grok_review"
    GROK_FAILED = "grok_failed"
    GROK_PASSED_WAITING_USER = "grok_passed_waiting_user"
    APPROVED = "approved"
    REJECTED = "rejected"
    ERROR = "error"
    PAUSED = "paused"


class ProjectStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class ReviewDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RunnerState(StrEnum):
    IDLE = "idle"
    STARTED = "started"
    RUNNING = "running"
    RETRY = "retry"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    BUSY = "busy"


@dataclass(slots=True)
class StructuredPromptResult:
    status: str
    wan_prompt: str = ""
    negative_prompt: str = ""
    motion_notes: str = ""
    raw_text: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(slots=True)
class ReviewResult:
    status: str
    review_result: str = ""
    reason: str = ""
    fix_notes: str = ""
    updated_wan_prompt: str = ""
    updated_negative_prompt: str = ""
    raw_text: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(slots=True)
class GenerationResult:
    status: str
    job_id: str = ""
    video_path: str = ""
    preview_path: str = ""
    raw_response: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(slots=True)
class JobStatusResult:
    status: str
    state: str = ""
    raw_response: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(slots=True)
class OutputArtifacts:
    status: str
    video_path: str = ""
    preview_path: str = ""
    raw_response: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(slots=True)
class Project:
    id: str
    title: str
    source_file: str
    status: str = ProjectStatus.PENDING.value
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class Shot:
    id: str
    project_id: str
    shot_number: int
    script_text: str
    positive_prompt: str
    negative_prompt: str = ""
    reference_image_path: str = ""
    depends_on_previous_shot: bool = True
    status: str = ShotStatus.PENDING.value
    approved_iteration_id: str | None = None


@dataclass(slots=True)
class ShotIteration:
    id: str
    shot_id: str
    iteration_number: int
    positive_prompt: str = ""
    negative_prompt: str = ""
    motion_notes: str = ""
    comfy_request_payload: str = ""
    output_video_path: str = ""
    output_preview_path: str = ""
    grok_review_raw: str = ""
    grok_review_status: str = ""
    grok_revision_notes: str = ""
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class Approval:
    shot_id: str
    iteration_id: str
    grok_passed_at: str
    user_decision: str | None = None
    user_decision_at: str | None = None


@dataclass(slots=True)
class ShotContext:
    project: Project
    shot: Shot
    previous_shot: Shot | None = None
    previous_iteration: ShotIteration | None = None
    resolved_reference_image_path: str = ""


@dataclass(slots=True)
class ControlEvent:
    event_type: str
    shot_id: str | None = None
    iteration_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectBundle:
    project: Project
    shots: list[Shot]


@dataclass(slots=True)
class RunnerResult:
    state: str
    project_id: str
    shot_id: str | None = None
    iteration_id: str | None = None
    error_code: str = ""
    error_message: str = ""


def artifact_path(base_dir: Path, project_id: str, shot_number: int, iteration_number: int) -> Path:
    return base_dir / "projects" / project_id / "shots" / f"{shot_number:03d}" / "iterations" / str(iteration_number)
