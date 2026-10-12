"""Background jobs for slow tools, so the caller never sits through dead air.

A job is started with a key (tool name + canonical arguments). Asking for the same thing
again while it runs returns the running job instead of doing the work twice, and a small
concurrency cap keeps one call from fanning out unbounded work. Failures are reported with
a generic message - internal errors are logged, never read aloud.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

FAILURE_MESSAGE = "That task could not be completed right now."


@dataclass
class Job:
    id: str
    key: str
    label: str
    started_at: float
    status: str = "running"  # running | done | failed
    result: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def elapsed(self, now: float | None = None) -> float:
        return (now if now is not None else time.monotonic()) - self.started_at


class JobManager:
    def __init__(self, max_running: int,
                 on_finish: Callable[[Job], Awaitable[None]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max = max_running
        self._on_finish = on_finish
        self._clock = clock
        self._ids = itertools.count(1)
        self._running: dict[str, Job] = {}

    @property
    def running(self) -> list[Job]:
        return list(self._running.values())

    def start(self, key: str, label: str,
              factory: Callable[[], Awaitable[str]]) -> tuple[str, Job | None]:
        if key in self._running:
            return "duplicate", self._running[key]
        if len(self._running) >= self._max:
            return "busy", None
        job = Job(id=f"job{next(self._ids)}", key=key, label=label, started_at=self._clock())
        self._running[key] = job
        job.task = asyncio.get_running_loop().create_task(self._run(job, factory))
        return "started", job

    async def _run(self, job: Job, factory: Callable[[], Awaitable[str]]) -> None:
        try:
            result = await factory()
            job.status, job.result = "done", str(result)
        except asyncio.CancelledError:
            self._running.pop(job.key, None)
            raise
        except Exception:
            log.exception("background job %s (%s) failed", job.id, job.label)
            job.status, job.result = "failed", FAILURE_MESSAGE
        self._running.pop(job.key, None)
        await self._on_finish(job)

    async def wait_idle(self) -> None:
        while self._running:
            await asyncio.gather(*(j.task for j in self.running if j.task),
                                 return_exceptions=True)

    async def cancel_all(self) -> None:
        tasks = [j.task for j in self.running if j.task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._running.clear()
