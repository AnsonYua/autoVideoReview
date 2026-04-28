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

        if event.event_type in {"menu", "help"}:
            self._send_menu()
            return True

        if event.event_type == "start_project":
            if self._targets_current_project(parts):
                self.orchestrator.start_project(self.project_id)
                self.runner.start(self.project_id)
            else:
                self._notify_project_mismatch()
            return True

        if event.event_type == "pause":
            if self._targets_current_project(parts):
                self.runner.request_pause(self.project_id)
            else:
                self._notify_project_mismatch()
            return True

        if event.event_type == "resume":
            if self._targets_current_project(parts):
                self.orchestrator.resume_project(self.project_id)
                self.runner.start(self.project_id)
            else:
                self._notify_project_mismatch()
            return True

        if event.event_type == "status":
            if self._targets_current_project(parts):
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

    def _targets_current_project(self, parts: list[str]) -> bool:
        return len(parts) == 1 or (len(parts) > 1 and parts[1] == self.project_id)

    def _send_menu(self) -> None:
        message = (
            "Menu\n"
            f"Project: {self.project_id}\n\n"
            "check status - show project status\n"
            "start project - start or continue the workflow\n"
            "pause project - pause the workflow\n"
            "resume project - resume the workflow\n"
            "/approve SHOT_ID ITERATION_ID - approve a reviewed shot\n"
            "/reject SHOT_ID ITERATION_ID - reject a reviewed shot\n"
            "/menu - show this menu"
        )
        reply_markup = {
            "keyboard": [
                [{"text": "check status"}],
                [{"text": "start project"}, {"text": "pause project"}],
                [{"text": "resume project"}, {"text": "show menu"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
        self.orchestrator.telegram_gateway.send_text(message, reply_markup)
