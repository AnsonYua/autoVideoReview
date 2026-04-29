from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    poll_timeout_seconds: int = 30


@dataclass(slots=True)
class GrokCliConfig:
    command: list[str] = field(default_factory=lambda: ["codex"])
    working_directory: Path = Path(".")
    timeout_seconds: int = 180
    review_script_path: Path = Path("tools/playwright_grok_review.py")
    review_first_landing: str = "https://grok.com/project/66b6fdb6-3ae4-4909-b421-59f7fc56ef09?chat=6c93a5e3-373d-4f12-b0df-7f2e588d2016&rid=40c486af-6668-4fbb-8d22-b9759e67ffc1"
    review_cdp_url: str = "http://127.0.0.1:9222"
    review_timeout_ms: int = 30000
    review_result_timeout_ms: int = 180000


@dataclass(slots=True)
class ComfyUIConfig:
    base_url: str = "http://127.0.0.1:8188"
    workflow_template_path: Path = Path("workflow.json")
    output_dir: Path = Path("outputs")
    poll_interval_seconds: float = 2.0
    timeout_seconds: int = 1800


@dataclass(slots=True)
class AppConfig:
    data_dir: Path = Path("data")
    storage_path: Path = Path("data/projects.json")
    grok_cli: GrokCliConfig = field(default_factory=GrokCliConfig)
    comfyui: ComfyUIConfig = field(default_factory=ComfyUIConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
