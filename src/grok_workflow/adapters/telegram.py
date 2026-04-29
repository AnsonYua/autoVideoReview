from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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

    def send_text(self, text: str, reply_markup: dict[str, object] | None = None) -> None:
        self._send_message(text, reply_markup)

    def request_approval(self, shot_id: str, iteration_id: str, preview_path: str) -> None:
        message = (
            f"Shot {shot_id} iteration {iteration_id} passed Grok review.\n"
            f"Preview: {preview_path}\n"
            f"Reply with /approve {shot_id} {iteration_id} or /reject {shot_id} {iteration_id}"
        )
        self._send_message(message)

    def send_video(self, video_path: str, caption: str) -> None:
        if not self.config.bot_token or not self.config.chat_id:
            return
        path = Path(video_path)
        if not path.exists():
            self._send_message(f"{caption}\nVideo file not found: {video_path}")
            return
        self._send_multipart("sendVideo", path, "video", {"chat_id": self.config.chat_id, "caption": caption})

    def consume_command(self) -> ControlEvent | None:
        updates = self._get_updates()
        for update in updates:
            self._last_update_id = max(self._last_update_id, int(update["update_id"]) + 1)
            callback_query = update.get("callback_query")
            if callback_query:
                callback_id = str(callback_query.get("id", ""))
                data = str(callback_query.get("data", "")).strip()
                sender = callback_query.get("from", {})
                sender_name = sender.get("username") or sender.get("first_name") or sender.get("id", "unknown")
                print(f"Telegram button from {sender_name}: {data}", flush=True)
                if callback_id:
                    self._answer_callback_query(callback_id)
                if data:
                    return self._parse_command(data)
                continue

            message = update.get("message", {})
            text = message.get("text", "").strip()
            if not text:
                continue
            sender = message.get("from", {})
            sender_name = sender.get("username") or sender.get("first_name") or sender.get("id", "unknown")
            print(f"Telegram message from {sender_name}: {text}", flush=True)
            return self._parse_command(text)
        return None

    def _parse_command(self, text: str) -> ControlEvent:
        parts = text.split()
        command = parts[0].lower()
        if not command.startswith("/"):
            normalized_text = text.lower()
            button_commands = {
                "menu": "menu",
                "show menu": "menu",
                "help": "menu",
                "check status": "check_status",
            }
            if normalized_text in button_commands:
                raw_text = "/check_status" if normalized_text == "check status" else text
                return ControlEvent(event_type=button_commands[normalized_text], payload={"raw_text": raw_text})
            return ControlEvent(event_type="message", payload={"raw_text": text})
        command = command.split("@", 1)[0]
        if command.startswith("/shot_"):
            return ControlEvent(event_type="shot", shot_id=command[1:], payload={"raw_text": text})
        if command in {"/approve", "/reject"} and len(parts) >= 3:
            return ControlEvent(
                event_type=command[1:],
                shot_id=parts[1],
                iteration_id=parts[2],
            )
        return ControlEvent(event_type=command[1:], payload={"raw_text": text})

    def _send_message(self, text: str, reply_markup: dict[str, object] | None = None) -> None:
        if not self.config.bot_token or not self.config.chat_id:
            return
        payload_data: dict[str, object] = {"chat_id": self.config.chat_id, "text": text}
        if reply_markup is not None:
            payload_data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        payload = urllib.parse.urlencode(payload_data).encode("utf-8")
        request = urllib.request.Request(self._api_url("sendMessage"), data=payload, method="POST")
        with urllib.request.urlopen(request):
            return

    def _answer_callback_query(self, callback_query_id: str) -> None:
        if not self.config.bot_token:
            return
        payload = urllib.parse.urlencode({"callback_query_id": callback_query_id}).encode("utf-8")
        request = urllib.request.Request(self._api_url("answerCallbackQuery"), data=payload, method="POST")
        with urllib.request.urlopen(request):
            return

    def _send_multipart(self, method: str, file_path: Path, file_field: str, fields: dict[str, str]) -> None:
        boundary = f"----grokWorkflowTelegram{int(time.time() * 1000)}"
        body_parts = []
        for key, value in fields.items():
            body_parts.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body_parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                file_path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        request = urllib.request.Request(
            self._api_url(method),
            data=b"".join(body_parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(request):
            return

    def _get_updates(self) -> list[dict[str, object]]:
        if not self.config.bot_token:
            return []
        query = urllib.parse.urlencode(
            {"timeout": self.config.poll_timeout_seconds, "offset": self._last_update_id}
        )
        try:
            with urllib.request.urlopen(f"{self._api_url('getUpdates')}?{query}") as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                time.sleep(5)
                return []
            raise
        return list(body.get("result", []))

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"

