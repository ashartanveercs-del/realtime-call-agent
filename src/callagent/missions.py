"""Outbound call "missions": the agent phones someone to pursue a goal and reports back.

A mission is created by the owner (over the API or mid-call via the `call_person` tool).
The callee leg runs as its own call session with the restricted "mission" role; when that
call ends, its transcript becomes the mission outcome and wakes whoever is waiting.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field


@dataclass
class Mission:
    id: str
    to: str
    goal: str
    requested_by: str
    status: str = "dialing"  # dialing | in_progress | completed | failed
    call_sid: str | None = None
    transcript: list[tuple[str, str]] = field(default_factory=list)
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _outcome: str | None = None

    async def outcome(self) -> str:
        await self._done.wait()
        return self._outcome or ""

    @property
    def result(self) -> str | None:
        return self._outcome

    def _settle(self, status: str, outcome: str) -> None:
        if self._done.is_set():
            return
        self.status = status
        self._outcome = outcome
        self._done.set()


class MissionRegistry:
    def __init__(self) -> None:
        self._missions: dict[str, Mission] = {}

    def create(self, *, to: str, goal: str, requested_by: str) -> Mission:
        mission = Mission(id=f"m_{secrets.token_hex(6)}", to=to, goal=goal,
                          requested_by=requested_by)
        self._missions[mission.id] = mission
        return mission

    def get(self, mission_id: str) -> Mission | None:
        return self._missions.get(mission_id)

    def finish(self, mission_id: str, outcome: str) -> None:
        mission = self._missions.get(mission_id)
        if mission is not None:
            mission._settle("completed", outcome)

    def fail(self, mission_id: str, reason: str) -> None:
        mission = self._missions.get(mission_id)
        if mission is not None:
            mission._settle("failed", reason)
