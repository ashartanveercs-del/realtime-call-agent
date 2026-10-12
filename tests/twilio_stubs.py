"""Stand-ins for the Twilio REST client (no network) and a recording gateway."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _Recorder:
    def __init__(self, prefix: str) -> None:
        self.created: list[dict[str, Any]] = []
        self._prefix = prefix

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(sid=f"{self._prefix}{len(self.created)}")


class StubTwilioClient:
    def __init__(self) -> None:
        self.calls = _Recorder("CA_OUT_")
        self.messages = _Recorder("SM_")


class RecordingGateway:
    """Gateway double for tool tests: records what would have been sent."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.texts: list[dict[str, Any]] = []

    def is_valid_request(self, url: str, params: dict[str, str], signature: str) -> bool:
        return True

    async def start_call(self, to: str, twiml: str, status_callback: str | None = None) -> str:
        self.calls.append({"to": to, "twiml": twiml, "status_callback": status_callback})
        return f"CA_OUT_{len(self.calls)}"

    async def send_sms(self, to: str, body: str) -> str:
        self.texts.append({"to": to, "body": body})
        return f"SM_{len(self.texts)}"
