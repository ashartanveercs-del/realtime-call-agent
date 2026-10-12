"""A tiny JSON-message socket interface so the call session can be tested without a network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from aiohttp import WSMsgType


class JsonSocket(Protocol):
    @property
    def closed(self) -> bool: ...

    async def send(self, obj: dict[str, Any]) -> None: ...

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]: ...

    async def close(self) -> None: ...


class AiohttpJsonSocket:
    """Adapts an aiohttp server or client WebSocket to `JsonSocket`."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    @property
    def closed(self) -> bool:
        return bool(self._ws.closed)

    async def send(self, obj: dict[str, Any]) -> None:
        if not self._ws.closed:
            await self._ws.send_str(json.dumps(obj))

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[dict[str, Any]]:
        async for msg in self._ws:
            if msg.type == WSMsgType.TEXT:
                yield json.loads(msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED,
                              WSMsgType.ERROR):
                break

    async def close(self) -> None:
        if not self._ws.closed:
            await self._ws.close()
