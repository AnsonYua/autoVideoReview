from __future__ import annotations

from grok_workflow.models import ControlEvent
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

        raw_text = str(event.payload.get("raw_text", "")).strip()
        parts = raw_text.split()

        if event.event_type == "start_project":
            if len(parts) > 1 and parts[1] == self.project_id:
                self.orchestrator.start_project(self.project_id)
                self.runner.start(self.project_id)
            else:
                self._notify_project_mismatch()
            return True

        if event.event_type == "pause":
            if len(parts) > 1 and parts[1] == self.project_id:
                self.runner.request_pause(self.project_id)
            else:
                self._notify_project_mismatch()
            return True

        if event.event_type == "resume":
            if len(parts) > 1 and parts[1] == self.project_id:
                self.orchestrator.resume_project(self.project_id)
                self.runner.start(self.project_id)
            else:
                self._notify_project_mismatch()
            return True

        if event.event_type == "status":
            if len(parts) > 1 and parts[1] == self.project_id:
                payload = self.orchestrator.get_project_status(self.project_id)
                payload["runner_state"] = self.runner.get_last_result().state
                self.orchestrator.telegram_gateway.notify("project_status", payload)
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

    def _notify_project_mismatch(self) -> None:
        self.orchestrator.telegram_gateway.notify(
            "project_error",
            {"error": "project_id_mismatch", "project_id": self.project_id},
        )
