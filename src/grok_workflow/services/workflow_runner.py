from __future__ import annotations

import threading

from grok_workflow.models import ApprovalDecision, ProjectStatus, RunnerResult, RunnerState
from grok_workflow.services.orchestrator import WorkflowOrchestrator


class WorkflowRunner:
    def __init__(self, orchestrator: WorkflowOrchestrator) -> None:
        self.orchestrator = orchestrator
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._last_result = RunnerResult(state=RunnerState.IDLE.value, project_id="")

    def start(self, project_id: str) -> RunnerResult:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return RunnerResult(state=RunnerState.BUSY.value, project_id=project_id)
            self._stop_requested.clear()
            self._last_result = RunnerResult(state=RunnerState.STARTED.value, project_id=project_id)
            self._thread = threading.Thread(target=self._run_project, args=(project_id,), daemon=True)
            self._thread.start()
            return self._last_result

    def request_pause(self, project_id: str) -> RunnerResult:
        self._stop_requested.set()
        self.orchestrator.pause_project(project_id)
        result = RunnerResult(state=RunnerState.PAUSED.value, project_id=project_id)
        with self._lock:
            self._last_result = result
        return result

    def handle_approval(self, project_id: str, shot_id: str, iteration_id: str, decision: str) -> RunnerResult:
        result = self.orchestrator.apply_approval_decision(project_id, shot_id, iteration_id, decision)
        if result.state == RunnerState.RUNNING.value:
            return self.start(project_id)
        with self._lock:
            self._last_result = result
        return result

    def start_shot_generation(self, project_id: str, shot_id: str) -> RunnerResult:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return RunnerResult(state=RunnerState.BUSY.value, project_id=project_id, shot_id=shot_id)
            self._stop_requested.clear()
            self._last_result = RunnerResult(state=RunnerState.STARTED.value, project_id=project_id, shot_id=shot_id)
            self._thread = threading.Thread(target=self._run_shot_generation, args=(project_id, shot_id), daemon=True)
            self._thread.start()
            return self._last_result

    def get_last_result(self) -> RunnerResult:
        with self._lock:
            return self._last_result

    def run_until_blocked(self, project_id: str) -> RunnerResult:
        while not self._stop_requested.is_set():
            shot = self.orchestrator.load_next_runnable_shot(project_id)
            if shot is None:
                project = self.orchestrator.storage.get_project(project_id)
                if project.status == ProjectStatus.WAITING_APPROVAL.value:
                    return RunnerResult(state=RunnerState.WAITING_APPROVAL.value, project_id=project_id)
                if self.orchestrator.storage.all_projects_completed(project_id):
                    self.orchestrator.complete_project(project_id)
                    return RunnerResult(state=RunnerState.COMPLETED.value, project_id=project_id)
                if project.status == ProjectStatus.PAUSED.value:
                    return RunnerResult(state=RunnerState.PAUSED.value, project_id=project_id)
                return RunnerResult(state=RunnerState.IDLE.value, project_id=project_id)

            result = self.orchestrator.execute_shot(project_id, shot)
            if result.state == RunnerState.RETRY.value:
                continue
            return result

        return RunnerResult(state=RunnerState.PAUSED.value, project_id=project_id)

    def _run_project(self, project_id: str) -> None:
        result = self.run_until_blocked(project_id)
        with self._lock:
            self._last_result = result
            self._thread = None

    def _run_shot_generation(self, project_id: str, shot_id: str) -> None:
        result = self.orchestrator.generate_shot_video(project_id, shot_id)
        with self._lock:
            self._last_result = result
            self._thread = None
