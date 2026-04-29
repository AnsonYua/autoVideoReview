from __future__ import annotations

import json
import mimetypes
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
        submission = self.submit_video(prompt_payload)
        if submission.status != "ok":
            return submission

        deadline = time.time() + self.config.timeout_seconds
        while time.time() < deadline:
            result = self.check_generation_result(submission.job_id)
            if result.status != "running":
                return result
            time.sleep(self.config.poll_interval_seconds)

        return GenerationResult(
            status="error",
            job_id=submission.job_id,
            error_code="timeout",
            error_message=f"ComfyUI job {submission.job_id} timed out",
        )

    def submit_video(self, prompt_payload: dict[str, object]) -> GenerationResult:
        try:
            workflow = json.loads(self.config.workflow_template_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return GenerationResult(
                status="error",
                error_code="workflow_template_missing",
                error_message=f"Workflow template not found: {self.config.workflow_template_path}",
            )

        image_name = self._prepare_reference_image(str(prompt_payload.get("reference_image_path", "")).strip())
        if image_name:
            prompt_payload = {**prompt_payload, "reference_image_path": image_name}
        try:
            request_payload = self._build_request_payload(workflow, prompt_payload)
        except (KeyError, TypeError, ValueError) as exc:
            return GenerationResult(
                status="error",
                error_code="workflow_payload_failed",
                error_message=str(exc),
            )
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

        return GenerationResult(
            status="ok",
            job_id=job_id,
            raw_response=json.dumps(prompt_result["data"]),
        )

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
        video_candidates = self._find_local_outputs(self.config.output_dir / "Wan2.2_Remix_V3", {".mp4", ".webm"})
        preview_candidates = self._find_local_outputs(
            self.config.output_dir / "Wan2.2_Remix_V3" / "last_frame",
            {".png", ".jpg", ".jpeg", ".webp"},
        )
        probed_video = self._probe_latest_numbered_output(
            subfolder="Wan2.2_Remix_V3",
            suffixes=(".mp4", ""),
            formats=("video/h264-mp4",),
            extension=".mp4",
        )
        if probed_video is not None:
            video_candidates.append(probed_video)
        probed_preview = self._probe_latest_numbered_output(
            subfolder="Wan2.2_Remix_V3/last_frame",
            suffixes=("_.png", ".png"),
            formats=("",),
            extension=".png",
        )
        if probed_preview is not None:
            preview_candidates.append(probed_preview)

        video_path = self._newest_existing_file(video_candidates)
        if video_path is None:
            return GenerationResult(
                status="error",
                error_code="missing_output",
                error_message="Could not recover a recent ComfyUI MP4 output.",
            )
        preview_path = self._newest_existing_file(preview_candidates)
        return GenerationResult(
            status="ok",
            video_path=str(video_path),
            preview_path=str(preview_path or ""),
        )

    def find_active_prompt_id(self) -> str:
        result = self._get_json("/queue")
        if result["status"] == "error":
            return ""
        data = result["data"]
        if not isinstance(data, dict):
            return ""
        prompt_ids = []
        for key in ("queue_running", "queue_pending"):
            for item in data.get(key, []):
                prompt_id = self._queue_item_prompt_id(item)
                if prompt_id:
                    prompt_ids.append(prompt_id)
        return prompt_ids[0] if len(prompt_ids) == 1 else ""

    def find_completed_prompt_id(self, prompt_payload: dict[str, object]) -> str:
        result = self._get_json("/history")
        if result["status"] == "error":
            return ""
        data = result["data"]
        if not isinstance(data, dict):
            return ""

        matches = []
        for prompt_id, record in data.items():
            if self._history_record_matches(record, prompt_payload):
                matches.append(str(prompt_id))
        return matches[-1] if matches else ""

    def _queue_item_prompt_id(self, item: object) -> str:
        if isinstance(item, dict):
            return str(item.get("prompt_id", "")).strip()
        if isinstance(item, list) and len(item) > 1:
            return str(item[1]).strip()
        return ""

    def _probe_latest_numbered_output(
        self,
        subfolder: str,
        suffixes: tuple[str, ...],
        formats: tuple[str, ...],
        extension: str,
        max_index: int = 100,
    ) -> Path | None:
        latest: Path | None = None
        for index in range(1, max_index + 1):
            for separator in (":", "_"):
                for suffix in suffixes:
                    filename = f"%date{separator}yyyyMMdd_hhmmss%_{index:05d}{suffix}"
                    artifact = {"filename": filename, "subfolder": subfolder, "type": "output"}
                    if formats[0]:
                        artifact["format"] = formats[0]
                    path = self._download_output_artifact(artifact)
                    if path is not None and path.exists() and path.stat().st_size > 0:
                        if not path.suffix and extension:
                            renamed = path.with_name(f"{path.name}{extension}")
                            path.replace(renamed)
                            path = renamed
                        latest = path
        return latest

    def _find_local_outputs(self, directory: Path, suffixes: set[str]) -> list[Path]:
        if not directory.exists():
            return []
        return [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in suffixes and path.stat().st_size > 0
        ]

    def _newest_existing_file(self, paths: list[Path]) -> Path | None:
        existing = [path for path in paths if path.exists() and path.is_file() and path.stat().st_size > 0]
        if not existing:
            return None
        return max(existing, key=lambda path: path.stat().st_mtime)

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
            if not video_path:
                for video in node_output.get("videos", []):
                    video_path = self._output_path(video)
                    break
            if not video_path:
                for gif in node_output.get("gifs", []):
                    video_path = self._output_path(gif)
                    break
            if not preview_path:
                for image in node_output.get("images", []):
                    preview_path = self._output_path(image)
                    break
            if video_path and preview_path:
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

    def _output_path(self, artifact: dict[str, object]) -> str:
        filename = self._safe_output_filename(artifact)
        subfolder = str(artifact.get("subfolder", "")).strip()
        downloaded = self._download_output_artifact(artifact)
        if downloaded is not None:
            return str(downloaded)
        if subfolder:
            return str(self.config.output_dir / subfolder / filename)
        return str(self.config.output_dir / filename)

    def _download_output_artifact(self, artifact: dict[str, object]) -> Path | None:
        filename = str(artifact.get("filename", "")).strip()
        if not filename:
            return None
        subfolder = str(artifact.get("subfolder", "")).strip()
        artifact_type = str(artifact.get("type", "output")).strip() or "output"
        target_dir = self.config.output_dir / subfolder if subfolder else self.config.output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / self._safe_output_filename(artifact)
        query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": artifact_type})
        try:
            with urllib.request.urlopen(f"{urllib.parse.urljoin(self.config.base_url, '/view')}?{query}") as response:
                target.write_bytes(response.read())
            return target
        except urllib.error.URLError:
            return target if target.exists() else None

    def _safe_output_filename(self, artifact: dict[str, object]) -> str:
        filename = self._safe_filename(str(artifact.get("filename", "")).strip())
        if Path(filename).suffix:
            return filename
        fmt = str(artifact.get("format", "")).lower()
        if "mp4" in fmt or "h264" in fmt:
            return f"{filename}.mp4"
        if "webm" in fmt:
            return f"{filename}.webm"
        return filename

    def _safe_filename(self, filename: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        sanitized = "".join("_" if char in invalid_chars else char for char in filename)
        return sanitized.strip().strip(".") or "comfy_output"

    def _history_record_matches(self, record: object, prompt_payload: dict[str, object]) -> bool:
        if not isinstance(record, dict):
            return False
        prompt_items = record.get("prompt", [])
        if not isinstance(prompt_items, list) or len(prompt_items) < 3 or not isinstance(prompt_items[2], dict):
            return False

        positive = str(prompt_payload.get("wan_prompt", "")).strip()
        negative = str(prompt_payload.get("negative_prompt", "")).strip()
        reference = Path(str(prompt_payload.get("reference_image_path", "")).strip()).name
        found_positive = False
        found_negative = False
        found_reference = not reference

        for node in prompt_items[2].values():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type", ""))
            inputs = node.get("inputs", {})
            meta = node.get("_meta", {})
            title = str(meta.get("title", "")).lower() if isinstance(meta, dict) else ""
            if not isinstance(inputs, dict):
                continue
            if class_type == "CLIPTextEncode":
                text = str(inputs.get("text", "")).strip()
                if positive and text == positive and "negative" not in title:
                    found_positive = True
                if negative and text == negative:
                    found_negative = True
            if class_type == "LoadImage" and reference:
                image = Path(str(inputs.get("image", "")).strip()).name
                if image == reference:
                    found_reference = True
        return found_positive and found_negative and found_reference

    def _build_request_payload(self, workflow: dict[str, object], prompt_payload: dict[str, object]) -> dict[str, object]:
        if "nodes" in workflow and "links" in workflow:
            workflow = self._editor_workflow_to_api_prompt(workflow)
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

    def _editor_workflow_to_api_prompt(self, workflow: dict[str, object]) -> dict[str, object]:
        links = {}
        for link in workflow.get("links", []):
            if isinstance(link, list) and len(link) >= 6:
                links[link[0]] = [str(link[1]), int(link[2])]

        prompt = {}
        for node in workflow.get("nodes", []):
            if not isinstance(node, dict) or node.get("mode") == 2:
                continue
            node_id = str(node["id"])
            inputs = {}
            for input_item in node.get("inputs", []):
                link_id = input_item.get("link")
                if link_id in links:
                    inputs[input_item["name"]] = links[link_id]
            inputs.update(self._widgets_to_inputs(str(node.get("type", "")), node.get("widgets_values", [])))
            prompt[node_id] = {
                "class_type": node.get("type", ""),
                "inputs": inputs,
                "_meta": {"title": node.get("title") or node.get("type", "")},
            }
        return prompt

    def _widgets_to_inputs(self, class_type: str, widgets: object) -> dict[str, object]:
        if isinstance(widgets, dict):
            if class_type == "VHS_VideoCombine":
                allowed = {
                    "frame_rate",
                    "loop_count",
                    "filename_prefix",
                    "format",
                    "pix_fmt",
                    "crf",
                    "save_metadata",
                    "trim_to_audio",
                    "pingpong",
                    "save_output",
                }
                return {key: value for key, value in widgets.items() if key in allowed}
            return {key: value for key, value in widgets.items() if key != "videopreview"}
        if not isinstance(widgets, list):
            return {}
        mappings = {
            "CLIPLoader": ["clip_name", "type", "device"],
            "KSamplerAdvanced": [
                "add_noise",
                "noise_seed",
                "",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
                "start_at_step",
                "end_at_step",
                "return_with_leftover_noise",
            ],
            "VAELoader": ["vae_name"],
            "UNETLoader": ["unet_name", "weight_dtype"],
            "CLIPVisionLoader": ["clip_name"],
            "CLIPVisionEncode": ["crop"],
            "SaveImage": ["filename_prefix"],
            "LoadImage": ["image"],
            "CLIPTextEncode": ["text"],
            "WanImageToVideo": ["width", "height", "length", "batch_size"],
            "ImageFromBatch": ["batch_index", "length"],
            "INTConstant": ["value"],
            "PrimitiveFloat": ["value"],
            "ModelSamplingSD3": ["shift"],
            "Seed (rgthree)": ["seed"],
        }
        names = mappings.get(class_type, [])
        return {name: widgets[index] for index, name in enumerate(names) if name and index < len(widgets)}

    def _prepare_reference_image(self, reference_image_path: str) -> str:
        if not reference_image_path:
            return ""
        path = Path(reference_image_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return reference_image_path
        upload_result = self._upload_image(path)
        if upload_result["status"] == "ok":
            data = upload_result["data"]
            return str(data.get("name") or path.name)
        return reference_image_path

    def _upload_image(self, path: Path) -> dict[str, object]:
        boundary = f"----grokWorkflow{int(time.time() * 1000)}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                path.read_bytes(),
                b"\r\n",
                f"--{boundary}\r\n".encode("utf-8"),
                b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        try:
            request = urllib.request.Request(
                urllib.parse.urljoin(self.config.base_url, "/upload/image"),
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                return {"status": "ok", "data": json.loads(response.read().decode("utf-8"))}
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            return {"status": "error", "error_code": "comfy_upload_failed", "error_message": str(exc)}

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
