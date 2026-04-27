from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from grok_workflow.models import Approval, Project, Shot, ShotIteration, ShotStatus


class Storage:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self._lock = threading.RLock()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write({"projects": []})

    def create_project(self, project: Project, shots: list[Shot]) -> None:
        with self._lock:
            data = self._read()
            data["projects"].append(
                {
                    "project": asdict(project),
                    "shots": [asdict(shot) for shot in shots],
                    "iterations": [],
                    "approvals": [],
                    "events": [],
                }
            )
            self._write(data)

    def get_project(self, project_id: str) -> Project:
        with self._lock:
            return Project(**self._project_record(project_id)["project"])

    def list_projects(self) -> list[Project]:
        with self._lock:
            return [Project(**record["project"]) for record in self._read()["projects"]]

    def get_latest_project(self) -> Project | None:
        projects = self.list_projects()
        return projects[-1] if projects else None

    def list_shots(self, project_id: str) -> list[Shot]:
        with self._lock:
            record = self._project_record(project_id)
            return [Shot(**item) for item in sorted(record["shots"], key=lambda shot: shot["shot_number"])]

    def get_shot(self, shot_id: str) -> Shot:
        with self._lock:
            for record in self._read()["projects"]:
                for shot in record["shots"]:
                    if shot["id"] == shot_id:
                        return Shot(**shot)
            raise KeyError(f"Unknown shot {shot_id}")

    def update_project_status(self, project_id: str, status: str) -> None:
        with self._lock:
            data = self._read()
            record = self._project_record_mut(data, project_id)
            record["project"]["status"] = status
            self._write(data)

    def update_shot(self, shot: Shot) -> None:
        with self._lock:
            data = self._read()
            record = self._project_record_mut(data, shot.project_id)
            for index, existing in enumerate(record["shots"]):
                if existing["id"] == shot.id:
                    record["shots"][index] = asdict(shot)
                    self._write(data)
                    return
            raise KeyError(f"Unknown shot {shot.id}")

    def create_iteration(self, iteration: ShotIteration) -> None:
        with self._lock:
            data = self._read()
            project_id = self._project_id_for_shot(data, iteration.shot_id)
            record = self._project_record_mut(data, project_id)
            record["iterations"].append(asdict(iteration))
            self._write(data)

    def update_iteration(self, iteration: ShotIteration) -> None:
        with self._lock:
            data = self._read()
            project_id = self._project_id_for_shot(data, iteration.shot_id)
            record = self._project_record_mut(data, project_id)
            for index, existing in enumerate(record["iterations"]):
                if existing["id"] == iteration.id:
                    record["iterations"][index] = asdict(iteration)
                    self._write(data)
                    return
            raise KeyError(f"Unknown iteration {iteration.id}")

    def list_iterations(self, shot_id: str) -> list[ShotIteration]:
        with self._lock:
            data = self._read()
            project_id = self._project_id_for_shot(data, shot_id)
            record = self._project_record(project_id, data)
            iterations = [ShotIteration(**item) for item in record["iterations"] if item["shot_id"] == shot_id]
            return sorted(iterations, key=lambda item: item.iteration_number)

    def get_iteration(self, iteration_id: str) -> ShotIteration:
        with self._lock:
            for record in self._read()["projects"]:
                for iteration in record["iterations"]:
                    if iteration["id"] == iteration_id:
                        return ShotIteration(**iteration)
            raise KeyError(f"Unknown iteration {iteration_id}")

    def save_approval(self, approval: Approval) -> None:
        with self._lock:
            data = self._read()
            project_id = self._project_id_for_shot(data, approval.shot_id)
            record = self._project_record_mut(data, project_id)
            for index, existing in enumerate(record["approvals"]):
                if existing["shot_id"] == approval.shot_id and existing["iteration_id"] == approval.iteration_id:
                    record["approvals"][index] = asdict(approval)
                    self._write(data)
                    return
            record["approvals"].append(asdict(approval))
            self._write(data)

    def get_next_runnable_shot(self, project_id: str) -> Shot | None:
        runnable_states = {ShotStatus.PENDING.value, ShotStatus.DRAFT_PROMPT_READY.value}
        for shot in self.list_shots(project_id):
            if shot.status in runnable_states:
                return shot
        return None

    def get_previous_approved_shot(self, project_id: str, shot_number: int) -> Shot | None:
        previous = [
            shot
            for shot in self.list_shots(project_id)
            if shot.shot_number < shot_number and shot.status == ShotStatus.APPROVED.value
        ]
        return previous[-1] if previous else None

    def get_selected_iteration(self, shot: Shot) -> ShotIteration | None:
        if not shot.approved_iteration_id:
            return None
        return self.get_iteration(shot.approved_iteration_id)

    def next_iteration_number(self, shot_id: str) -> int:
        iterations = self.list_iterations(shot_id)
        return (iterations[-1].iteration_number if iterations else 0) + 1

    def record_event(self, project_id: str, event_type: str, payload: str, shot_id: str | None = None, iteration_id: str | None = None) -> None:
        with self._lock:
            data = self._read()
            record = self._project_record_mut(data, project_id)
            record["events"].append(
                {
                    "event_type": event_type,
                    "payload": payload,
                    "shot_id": shot_id,
                    "iteration_id": iteration_id,
                }
            )
            self._write(data)

    def all_projects_completed(self, project_id: str) -> bool:
        shots = self.list_shots(project_id)
        return bool(shots) and all(shot.status == ShotStatus.APPROVED.value for shot in shots)

    def _read(self) -> dict[str, Any]:
        return json.loads(self.storage_path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.storage_path.parent, delete=False) as tmp_file:
            tmp_file.write(serialized)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = Path(tmp_file.name)
        os.replace(tmp_path, self.storage_path)

    def _project_record(self, project_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = data or self._read()
        for record in payload["projects"]:
            if record["project"]["id"] == project_id:
                return record
        raise KeyError(f"Unknown project {project_id}")

    def _project_record_mut(self, data: dict[str, Any], project_id: str) -> dict[str, Any]:
        for record in data["projects"]:
            if record["project"]["id"] == project_id:
                return record
        raise KeyError(f"Unknown project {project_id}")

    def _project_id_for_shot(self, data: dict[str, Any], shot_id: str) -> str:
        for record in data["projects"]:
            for shot in record["shots"]:
                if shot["id"] == shot_id:
                    return record["project"]["id"]
        raise KeyError(f"Unknown shot {shot_id}")
