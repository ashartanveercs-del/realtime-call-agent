"""In-memory stand-ins for the Twilio media stream and the Realtime model socket."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from typing import Any

_CLOSE = object()


class FakeSocket:
    """Implements the JsonSocket interface. `push()` delivers a message to the session;
    `sent` records everything the session sent. With `auto_ack_responses`, every
    `response.create` is answered with `response.created` + `response.done`, like an
    instantly-finishing model."""

    def __init__(self, auto_ack_responses: bool = False) -> None:
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.auto_ack = auto_ack_responses
        self._closed = False
        self._responses = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def push(self, obj: dict[str, Any]) -> None:
        self.inbox.put_nowait(obj)

    async def send(self, obj: dict[str, Any]) -> None:
        if self._closed:
            return
        self.sent.append(obj)
        if self.auto_ack and obj.get("type") == "response.create":
            self._responses += 1
            rid = f"resp{self._responses}"
            self.push({"type": "response.created", "response": {"id": rid}})
            self.push({"type": "response.done", "response": {"id": rid, "usage": {}}})

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        while True:
            item = await self.inbox.get()
            if item is _CLOSE:
                return
            yield item

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.inbox.put_nowait(_CLOSE)

    def matching(self, predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
        return [m for m in self.sent if predicate(m)]

    async def wait_for(self, predicate: Callable[[dict[str, Any]], bool],
                       timeout: float = 2.0, after: int = 0) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for msg in self.sent[after:]:
                if predicate(msg):
                    return msg
            if loop.time() > deadline:
                raise AssertionError(f"timed out waiting; last sent: {self.sent[-8:]}")
            await asyncio.sleep(0.005)

    async def wait_closed(self, timeout: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._closed:
            if loop.time() > deadline:
                raise AssertionError("socket was not closed")
            await asyncio.sleep(0.005)


class FakeConnector:
    def __init__(self, auto_ack_responses: bool = True) -> None:
        self.sockets: list[FakeSocket] = []
        self._auto_ack = auto_ack_responses
        self._connected = asyncio.Event()

    async def connect(self) -> FakeSocket:
        sock = FakeSocket(self._auto_ack)
        self.sockets.append(sock)
        self._connected.set()
        return sock

    async def wait_model(self, timeout: float = 2.0) -> FakeSocket:
        await asyncio.wait_for(self._connected.wait(), timeout)
        return self.sockets[-1]


class ManualClock:
    """A monotonic clock that only moves when the test says so, for the session's timers."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def start_event(token: str, stream_sid: str = "MZ1", call_sid: str = "CA1") -> dict[str, Any]:
    return {"event": "start", "streamSid": stream_sid,
            "start": {"streamSid": stream_sid, "callSid": call_sid,
                      "customParameters": {"token": token}}}


def media_event(payload: str = "f39/", ts: int = 0) -> dict[str, Any]:
    return {"event": "media", "streamSid": "MZ1",
            "media": {"track": "inbound", "timestamp": str(ts), "payload": payload}}


def audio_delta(item_id: str, nbytes: int) -> dict[str, Any]:
    return {"type": "response.output_audio.delta", "item_id": item_id,
            "delta": base64.b64encode(b"\xff" * nbytes).decode()}


def is_type(kind: str) -> Callable[[dict[str, Any]], bool]:
    return lambda m: m.get("type") == kind or m.get("event") == kind
