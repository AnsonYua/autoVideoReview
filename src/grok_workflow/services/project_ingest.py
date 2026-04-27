from __future__ import annotations

import json
import re
from pathlib import Path

from grok_workflow.models import Project, ProjectBundle, Shot, new_id


class ProjectIngestService:
    def import_file(self, file_path: str | Path) -> ProjectBundle:
        path = Path(file_path)
        if path.suffix.lower() == ".json":
            return self.import_json(path)
        return self.import_txt(path)

    def import_json(self, file_path: str | Path) -> ProjectBundle:
        path = Path(file_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        project = Project(
            id=new_id("project"),
            title=str(payload.get("title") or path.stem),
            source_file=str(path),
            status=str(payload.get("status", "pending")),
        )
        shots: list[Shot] = []
        for index, item in enumerate(payload.get("shots", []), start=1):
            shots.append(
                Shot(
                    id=new_id("shot"),
                    project_id=project.id,
                    shot_number=int(item.get("shot_number", index)),
                    script_text=str(item.get("script_text", "")),
                    positive_prompt=str(item.get("positive_prompt", "")),
                    negative_prompt=str(item.get("negative_prompt", "")),
                    reference_image_path=str(item.get("reference_image_path", "")),
                    depends_on_previous_shot=bool(item.get("depends_on_previous_shot", True)),
                    status=str(item.get("status", "pending")),
                    approved_iteration_id=item.get("approved_iteration_id"),
                )
            )
        if not shots:
            raise ValueError("JSON project must contain at least one shot")
        return ProjectBundle(project=project, shots=shots)

    def import_txt(self, file_path: str | Path) -> ProjectBundle:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        sections = [section.strip() for section in re.split(r"^=== SHOT.*?===\s*$", content, flags=re.MULTILINE) if section.strip()]
        if not sections:
            raise ValueError("No shots found in input file")

        title = path.stem
        if content.startswith("TITLE:"):
            title = content.splitlines()[0].split(":", 1)[1].strip() or title
            if sections and sections[0].startswith("TITLE:"):
                sections = sections[1:]

        project = Project(id=new_id("project"), title=title, source_file=str(path))
        shots: list[Shot] = []
        for index, section in enumerate(sections, start=1):
            shots.append(
                Shot(
                    id=new_id("shot"),
                    project_id=project.id,
                    shot_number=index,
                    script_text=self._extract_field(section, "SCRIPT"),
                    positive_prompt=self._extract_field(section, "PROMPT"),
                    negative_prompt=self._extract_field(section, "NEGATIVE_PROMPT", required=False),
                    reference_image_path=self._extract_field(section, "REFERENCE_IMAGE_PATH", required=False),
                )
            )
        return ProjectBundle(project=project, shots=shots)

    def _extract_field(self, section: str, field_name: str, required: bool = True) -> str:
        marker = f"{field_name}:"
        lines = section.splitlines()
        active = False
        buffer: list[str] = []
        for line in lines:
            if line.startswith("==="):
                break
            if line.startswith(marker):
                active = True
                buffer.append(line.split(":", 1)[1].strip())
                continue
            if active and ":" in line and line.split(":", 1)[0].isupper():
                break
            if active:
                buffer.append(line.strip())
        result = "\n".join(part for part in buffer if part).strip()
        if required and not result:
            raise ValueError(f"Missing field {field_name}")
        return result
