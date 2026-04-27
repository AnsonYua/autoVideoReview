from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from grok_workflow.adapters.base import ComfyAdapter
from grok_workflow.config import ComfyUIConfig
from grok_workflow.models import GenerationResult, JobStatusResult, OutputArtifacts


class ComfyUIApiAdapter(ComfyAdapter):
    def __init__(self, config: ComfyUIConfig) -> None:
        self.config = config

    def generate_video(self, prompt_payload: dict[str, object]) -> GenerationResult:
        try:
            workflow = json.loads(self.config.workflow_template_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return GenerationResult(
                status="error",
                error_code="workflow_template_missing",
                error_message=f"Workflow template not found: {self.config.workflow_template_path}",
            )

        request_payload = self._build_request_payload(workflow, prompt_payload)
        prompt_result = self._post_json("/prompt", request_payload)
        if prompt_result["status"] == "error":
            return GenerationResult(**prompt_result)

        job_id = str(prompt_result["data"].get("prompt_id", ""))
        if not job_id:
            return GenerationResult(
                status="error",
                error_code="missing_job_id",
                error_message="ComfyUI did not return prompt_id",
                raw_response=json.dumps(prompt_result["data"]),
            )

        deadline = time.time() + self.config.timeout_seconds
        while time.time() < deadline:
            status = self.get_job_status(job_id)
            if status.status == "error":
                return GenerationResult(
                    status="error",
                    error_code=status.error_code,
                    error_message=status.error_message,
                    raw_response=status.raw_response,
                )
            if status.state == "completed":
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
            time.sleep(self.config.poll_interval_seconds)

        return GenerationResult(
            status="error",
            job_id=job_id,
            error_code="timeout",
            error_message=f"ComfyUI job {job_id} timed out",
        )

    def get_job_status(self, job_id: str) -> JobStatusResult:
        result = self._get_json(f"/history/{urllib.parse.quote(job_id)}")
        if result["status"] == "error":
            return JobStatusResult(
                status="error",
                error_code=result["error_code"],
                error_message=result["error_message"],
                raw_response=result.get("raw_response", ""),
            )

        data = result["data"]
        if job_id not in data:
            return JobStatusResult(status="ok", state="queued", raw_response=json.dumps(data))
        return JobStatusResult(status="ok", state="completed", raw_response=json.dumps(data[job_id]))

    def collect_outputs(self, job_id: str) -> OutputArtifacts:
        result = self._get_json(f"/history/{urllib.parse.quote(job_id)}")
        if result["status"] == "error":
            return OutputArtifacts(
                status="error",
                error_code=result["error_code"],
                error_message=result["error_message"],
                raw_response=result.get("raw_response", ""),
            )

        data = result["data"].get(job_id, {})
        outputs = data.get("outputs", {})
        video_path = ""
        preview_path = ""
        for node_output in outputs.values():
            for video in node_output.get("videos", []):
                video_path = str(self.config.output_dir / video["filename"])
                break
            for image in node_output.get("images", []):
                preview_path = str(self.config.output_dir / image["filename"])
                break
            if video_path or preview_path:
                break

        if not video_path:
            return OutputArtifacts(
                status="error",
                error_code="missing_output",
                error_message=f"No video output found for job {job_id}",
                raw_response=json.dumps(data),
            )
        return OutputArtifacts(
            status="ok",
            video_path=video_path,
            preview_path=preview_path,
            raw_response=json.dumps(data),
        )

    def _build_request_payload(self, workflow: dict[str, object], prompt_payload: dict[str, object]) -> dict[str, object]:
        request_payload = {"prompt": workflow}
        prompt_node = self._find_text_input_node(workflow)
        if prompt_node is None:
            raise ValueError("Workflow template missing a text input node")
        prompt_node["inputs"]["text"] = prompt_payload["wan_prompt"]
        if "negative_prompt" in prompt_payload:
            negative_node = self._find_text_input_node(workflow, preferred_title="negative")
            if negative_node is not None:
                negative_node["inputs"]["text"] = prompt_payload["negative_prompt"]
        reference_image_path = str(prompt_payload.get("reference_image_path", "")).strip()
        if reference_image_path:
            reference_node = self._find_load_image_node(workflow)
            if reference_node is not None:
                reference_node["inputs"]["image"] = reference_image_path
        return request_payload

    def _find_text_input_node(self, workflow: dict[str, object], preferred_title: str | None = None) -> dict[str, object] | None:
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type", "")).lower()
            meta = node.get("_meta", {})
            title = str(meta.get("title", "")).lower() if isinstance(meta, dict) else ""
            if class_type == "cliptextencode" and (preferred_title is None or preferred_title in title):
                return node
        return None

    def _find_load_image_node(self, workflow: dict[str, object]) -> dict[str, object] | None:
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type", "")).lower()
            meta = node.get("_meta", {})
            title = str(meta.get("title", "")).lower() if isinstance(meta, dict) else ""
            if class_type == "loadimage" and any(keyword in title for keyword in ("reference", "ref", "start")):
                return node
        return None

    def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            request = urllib.request.Request(
                urllib.parse.urljoin(self.config.base_url, path),
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                body = response.read().decode("utf-8")
            return {"status": "ok", "data": json.loads(body)}
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            return {
                "status": "error",
                "error_code": "comfy_request_failed",
                "error_message": str(exc),
                "raw_response": "",
            }

    def _get_json(self, path: str) -> dict[str, object]:
        try:
            request = urllib.request.Request(urllib.parse.urljoin(self.config.base_url, path), method="GET")
            with urllib.request.urlopen(request) as response:
                body = response.read().decode("utf-8")
            return {"status": "ok", "data": json.loads(body)}
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "error_code": "comfy_request_failed",
                "error_message": str(exc),
                "raw_response": "",
            }
