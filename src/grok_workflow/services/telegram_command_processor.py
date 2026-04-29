from __future__ import annotations

from grok_workflow.models import ControlEvent
from grok_workflow.models import ShotStatus
from grok_workflow.services.orchestrator import WorkflowOrchestrator
from grok_workflow.services.workflow_runner import WorkflowRunner


class TelegramCommandProcessor:
    def __init__(self, orchestrator: WorkflowOrchestrator, runner: WorkflowRunner, project_id: str) -> None:
        self.orchestrator = orchestrator
        self.runner = runner
        self.project_id = project_id

    def process_next_command(self) -> bool:
        event = self.orchestrator.telegram_gateway.consume_command()
        if event is None:
            return False

        if event.event_type in {"approve", "reject"}:
            self._handle_approval_event(event)
            return True

        if event.event_type == "shot":
            self._handle_shot_event(event)
            return True

        raw_text = str(event.payload.get("raw_text", "")).strip()
        parts = raw_text.split()

        if event.event_type in {"menu", "help"}:
            self._send_menu()
            return True

        if event.event_type in {"check_status", "status"}:
            if self._targets_current_project(parts):
                if self._refresh_active_wan_generation():
                    return True
                if self._review_generated_shot_if_needed():
                    return True
                self._send_shot_json_lines()
            else:
                self.orchestrator.telegram_gateway.notify(
                    "project_status",
                    {"error": "project_id_mismatch", "project_id": self.project_id},
                )
            return True

        return True

    def _handle_approval_event(self, event: ControlEvent) -> None:
        if not event.shot_id or not event.iteration_id:
            self.orchestrator.telegram_gateway.notify(
                "project_error",
                {"error": "missing_shot_or_iteration_id", "project_id": self.project_id},
            )
            return

        shot = self.orchestrator.storage.get_shot(event.shot_id)
        if shot.project_id != self.project_id:
            self._notify_project_mismatch()
            return
        self.runner.handle_approval(self.project_id, event.shot_id, event.iteration_id, event.event_type)

    def _handle_shot_event(self, event: ControlEvent) -> None:
        if not event.shot_id:
            self.orchestrator.telegram_gateway.send_text("Missing shot id.")
            return

        startable_shot = self._startable_pending_shot()
        if startable_shot is None:
            active_shot = self._active_shot()
            if active_shot is not None:
                self.orchestrator.telegram_gateway.send_text(
                    f"Cannot start another shot now.\n"
                    f"Active shot: {active_shot.id}\n"
                    f"status : {active_shot.status}"
                )
                return
            self.orchestrator.telegram_gateway.send_text("No pending shot found.")
            return
        if event.shot_id != startable_shot.id:
            self.orchestrator.telegram_gateway.send_text(
                f"Only the first pending shot can start now: /{startable_shot.id}"
            )
            return

        result = self.runner.start_shot_generation(self.project_id, event.shot_id)
        if result.error_code:
            self.orchestrator.telegram_gateway.send_text(f"Cannot start shot: {result.error_message}")
        elif result.state == "busy":
            self.orchestrator.telegram_gateway.send_text("A shot is already running. Please wait for it to finish.")

    def _notify_project_mismatch(self) -> None:
        self.orchestrator.telegram_gateway.notify(
            "project_error",
            {"error": "project_id_mismatch", "project_id": self.project_id},
        )

    def _targets_current_project(self, parts: list[str]) -> bool:
        return len(parts) == 1 or (len(parts) > 1 and parts[1] == self.project_id)

    def _send_menu(self) -> None:
        message = (
            "Menu\n"
            f"Project: {self.project_id}\n\n"
            "/check_status or check status - print project shot prompts\n"
            "/approve SHOT_ID ITERATION_ID - approve a reviewed shot\n"
            "/reject SHOT_ID ITERATION_ID - reject a reviewed shot\n"
            "/menu - show this menu"
        )
        self.orchestrator.telegram_gateway.send_text(message)

    def _send_shot_json_lines(self) -> None:
        shots = self.orchestrator.storage.list_shots(self.project_id)
        if not shots:
            self.orchestrator.telegram_gateway.send_text(f"No shots found for {self.project_id}")
            return

        startable_shot = self._startable_pending_shot(shots)
        blocks = []
        for shot in shots:
            shot_id = f"/{shot.id}" if startable_shot is not None and shot.id == startable_shot.id else shot.id
            blocks.append(
                "\n".join(
                    [
                        f"id : {shot_id}",
                        f"shot_number: {shot.shot_number}",
                        f"positive_prompt: {shot.positive_prompt}",
                        f"negative_prompt: {shot.negative_prompt}",
                        f"reference_image_path: {shot.reference_image_path}",
                        f"status : {shot.status}",
                    ]
                )
            )
        self._send_chunked_text("\n\n".join(blocks))

    def _send_chunked_text(self, text: str, chunk_size: int = 3800) -> None:
        chunk = ""
        for line in text.splitlines():
            candidate = f"{chunk}\n{line}" if chunk else line
            if len(candidate) > chunk_size:
                if chunk:
                    self.orchestrator.telegram_gateway.send_text(chunk)
                chunk = line
            else:
                chunk = candidate
        if chunk:
            self.orchestrator.telegram_gateway.send_text(chunk)

    def _refresh_active_wan_generation(self) -> bool:
        active_shot = self._active_shot()
        if active_shot is None or active_shot.status != ShotStatus.SENT_TO_COMFY.value:
            return False

        result = self.orchestrator.refresh_wan_generation_status(self.project_id, active_shot.id)
        if result.state == "running":
            self.orchestrator.telegram_gateway.send_text(
                f"WAN working for {active_shot.id}\n"
                f"status : {ShotStatus.SENT_TO_COMFY.value}"
            )
            return True
        if result.state in {"retry", "waiting_approval"}:
            return True
        if result.state == "error":
            self.orchestrator.telegram_gateway.send_text(
                f"WAN status check failed for {active_shot.id}\n"
                f"error_code : {result.error_code}\n"
                f"error_message : {result.error_message}"
            )
            return True
        return False

    def _review_generated_shot_if_needed(self) -> bool:
        shots = self.orchestrator.storage.list_shots(self.project_id)
        shot = next(
            (
                item
                for item in shots
                if item.status in {ShotStatus.VIDEO_GENERATED.value, ShotStatus.SENT_TO_GROK_REVIEW.value}
            ),
            None,
        )
        if shot is None:
            return False
        result = self.orchestrator.review_generated_shot(self.project_id, shot.id)
        if result.state == "retry":
            self._send_shot_json_lines()
            return True
        if result.state == "waiting_approval":
            return True
        if result.state == "error":
            self.orchestrator.telegram_gateway.send_text(
                f"Grok review could not start for {shot.id}\n"
                f"error_code : {result.error_code}\n"
                f"error_message : {result.error_message}"
            )
            return True
        return False

    def _startable_pending_shot(self, shots=None):
        if shots is None:
            shots = self.orchestrator.storage.list_shots(self.project_id)
        if self._active_shot(shots) is not None:
            return None
        return next((shot for shot in shots if shot.status == ShotStatus.PENDING.value), None)

    def _active_shot(self, shots=None):
        if shots is None:
            shots = self.orchestrator.storage.list_shots(self.project_id)
        active_statuses = {
            ShotStatus.SENT_TO_GROK.value,
            ShotStatus.GROK_PROMPT_RECEIVED.value,
            ShotStatus.SENT_TO_COMFY.value,
            ShotStatus.SENT_TO_GROK_REVIEW.value,
            ShotStatus.GROK_PASSED_WAITING_USER.value,
            ShotStatus.PAUSED.value,
            ShotStatus.ERROR.value,
        }
        return next((shot for shot in shots if shot.status in active_statuses), None)
