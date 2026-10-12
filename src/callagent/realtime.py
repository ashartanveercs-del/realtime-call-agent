"""OpenAI Realtime API (GA) protocol helpers and the WebSocket connector.

Audio stays G.711 mu-law (`audio/pcmu`) end to end: Twilio Media Streams deliver mu-law
8 kHz frames and the Realtime API accepts and emits the same format, so the bridge never
transcodes - frames are relayed as base64 strings untouched.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import aiohttp

from .sockets import AiohttpJsonSocket, JsonSocket


def session_update(*, instructions: str, tools: list[dict[str, Any]], voice: str,
                   transcription_model: str | None = None,
                   vad_silence_ms: int = 500) -> dict[str, Any]:
    audio_input: dict[str, Any] = {
        "format": {"type": "audio/pcmu"},
        "turn_detection": {"type": "server_vad", "silence_duration_ms": vad_silence_ms},
    }
    if transcription_model:
        audio_input["transcription"] = {"model": transcription_model}
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": audio_input,
                "output": {"format": {"type": "audio/pcmu"}, "voice": voice},
            },
            "tools": tools,
            "tool_choice": "auto",
        },
    }


def append_audio(b64_payload: str) -> dict[str, Any]:
    return {"type": "input_audio_buffer.append", "audio": b64_payload}


def tool_output(call_id: str, output: Any) -> dict[str, Any]:
    text = output if isinstance(output, str) else json.dumps(output)
    return {"type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id, "output": text}}


def function_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A function_call item added by the client. A result that arrives later (a background
    job) is attached to one, so the model reads it as tool output - not as instructions."""
    return {"type": "conversation.item.create",
            "item": {"type": "function_call", "call_id": call_id, "name": name,
                     "arguments": json.dumps(arguments)}}


def create_response(instructions: str | None = None) -> dict[str, Any]:
    if instructions is None:
        return {"type": "response.create"}
    return {"type": "response.create", "response": {"instructions": instructions}}


def cancel_response() -> dict[str, Any]:
    return {"type": "response.cancel"}


def truncate(item_id: str, audio_end_ms: int) -> dict[str, Any]:
    return {"type": "conversation.item.truncate", "item_id": item_id,
            "content_index": 0, "audio_end_ms": max(0, int(audio_end_ms))}


def system_note(text: str) -> dict[str, Any]:
    return {"type": "conversation.item.create",
            "item": {"type": "message", "role": "system",
                     "content": [{"type": "input_text", "text": text}]}}


@dataclass
class Usage:
    input_text: int = 0
    input_audio: int = 0
    cached: int = 0
    output_text: int = 0
    output_audio: int = 0

    def add_response_done(self, event: dict[str, Any]) -> None:
        usage = (event.get("response") or {}).get("usage") or {}
        inp = usage.get("input_token_details") or {}
        out = usage.get("output_token_details") or {}
        self.input_text += int(inp.get("text_tokens") or 0)
        self.input_audio += int(inp.get("audio_tokens") or 0)
        self.cached += int(inp.get("cached_tokens") or 0)
        self.output_text += int(out.get("text_tokens") or 0)
        self.output_audio += int(out.get("audio_tokens") or 0)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def cost(self, prices_per_mtok: dict[str, float]) -> float | None:
        """Estimated USD cost given prices per 1M tokens keyed like `as_dict()`."""
        if not prices_per_mtok:
            return None
        tokens = self.as_dict()
        total = sum(tokens.get(k, 0) * price for k, price in prices_per_mtok.items())
        return round(total / 1_000_000, 6)


class RealtimeConnector(Protocol):
    async def connect(self) -> JsonSocket: ...


class OpenAIRealtimeConnector:
    """Opens one Realtime WebSocket per call."""

    def __init__(self, api_key: str, model: str, url: str,
                 http: aiohttp.ClientSession | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._url = url
        self._http = http

    async def connect(self) -> JsonSocket:
        if self._http is None:
            self._http = aiohttp.ClientSession()
        ws = await self._http.ws_connect(
            self._url, params={"model": self._model},
            headers={"Authorization": f"Bearer {self._api_key}"},
            heartbeat=20, max_msg_size=0,
        )
        return AiohttpJsonSocket(ws)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
