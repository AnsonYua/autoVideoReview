from __future__ import annotations

import json
import urllib.parse
import urllib.request

from grok_workflow.adapters.base import TelegramGateway
from grok_workflow.config import TelegramConfig
from grok_workflow.models import ControlEvent


class TelegramBotGateway(TelegramGateway):
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self._last_update_id = 0

    def notify(self, event_type: str, payload: dict[str, object]) -> None:
        message = json.dumps({"event_type": event_type, "payload": payload}, ensure_ascii=False)
        self._send_message(message)

    def request_approval(self, shot_id: str, iteration_id: str, preview_path: str) -> None:
        message = (
            f"Shot {shot_id} iteration {iteration_id} passed Grok review.\n"
            f"Preview: {preview_path}\n"
            f"Reply with /approve {shot_id} {iteration_id} or /reject {shot_id} {iteration_id}"
        )
        self._send_message(message)

    def consume_command(self) -> ControlEvent | None:
        updates = self._get_updates()
        for update in updates:
            self._last_update_id = max(self._last_update_id, int(update["update_id"]) + 1)
            message = update.get("message", {})
            text = message.get("text", "").strip()
            if not text.startswith("/"):
                continue
            return self._parse_command(text)
        return None

    def _parse_command(self, text: str) -> ControlEvent:
        parts = text.split()
        command = parts[0].lower()
        if command in {"/approve", "/reject"} and len(parts) >= 3:
            return ControlEvent(
                event_type=command[1:],
                shot_id=parts[1],
                iteration_id=parts[2],
            )
        return ControlEvent(event_type=command[1:], payload={"raw_text": text})

    def _send_message(self, text: str) -> None:
        if not self.config.bot_token or not self.config.chat_id:
            return
        payload = urllib.parse.urlencode({"chat_id": self.config.chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(self._api_url("sendMessage"), data=payload, method="POST")
        with urllib.request.urlopen(request):
            return

    def _get_updates(self) -> list[dict[str, object]]:
        if not self.config.bot_token:
            return []
        query = urllib.parse.urlencode(
            {"timeout": self.config.poll_timeout_seconds, "offset": self._last_update_id}
        )
        with urllib.request.urlopen(f"{self._api_url('getUpdates')}?{query}") as response:
            body = json.loads(response.read().decode("utf-8"))
        return list(body.get("result", []))

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

