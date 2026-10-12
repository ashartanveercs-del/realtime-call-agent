"""Tracks what the caller has actually *heard*, which is what barge-in truncation needs.

The model streams audio much faster than real time, so "bytes sent to Twilio" overstates
what was played. Twilio's inbound media frames carry a stream-clock timestamp (ms since the
stream started), and Twilio plays the audio we send back to back, in order. So every chunk
gets a slot on a playout timeline: it starts when the audio queued before it runs out (or
when it arrives, if the line was idle) and lasts as long as its audio. What the caller has
heard of a reply is the part of its slots the stream clock has already passed. That stays
true when a second reply is queued behind the first, and when the line briefly runs dry.

Marks: after each audio chunk we send a Twilio `mark`; Twilio echoes it back once the audio
before it has finished playing. Pending marks == audio still queued on the caller's line.
When the last one comes back, everything sent has played and the timeline restarts there.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

ULAW_BYTES_PER_MS = 8  # G.711 mu-law, 8 kHz, 8-bit mono


def ulaw_ms(nbytes: int) -> int:
    return nbytes // ULAW_BYTES_PER_MS


@dataclass
class _Reply:
    """One assistant audio item that is (or was recently) on the line."""

    slots: list[list[float]] = field(default_factory=list)  # [start, end] on the stream clock
    played_ms: float = 0.0  # audio known to be over, or sent before the stream clock started
    complete: bool = False  # the model has finished producing this item's audio

    @property
    def sent_ms(self) -> float:
        return self.played_ms + sum(end - start for start, end in self.slots)

    def heard_ms(self, now: float | None) -> float:
        if now is None:  # no stream clock yet: assume what was sent has played
            return self.sent_ms
        return self.played_ms + sum(min(max(now - start, 0.0), end - start)
                                    for start, end in self.slots)


class PlayoutTracker:
    def __init__(self) -> None:
        self._now: int | None = None  # latest stream-clock timestamp from Twilio (ms)
        self._queue_end: float | None = None  # when the audio queued so far finishes playing
        self._replies: dict[str, _Reply] = {}  # in the order their audio was sent
        self._marks: set[str] = set()
        self._counter = itertools.count(1)

    @property
    def pending_marks(self) -> int:
        return len(self._marks)

    def on_twilio_timestamp(self, ts_ms: int) -> None:
        self._now = ts_ms

    def on_audio_sent(self, item_id: str, nbytes: int) -> None:
        reply = self._replies.get(item_id)
        if reply is None:
            reply = self._replies[item_id] = _Reply()
        duration = nbytes / ULAW_BYTES_PER_MS
        if self._now is None:
            reply.played_ms += duration
            return
        start = self._now if self._queue_end is None else max(self._now, self._queue_end)
        end = start + duration
        if reply.slots and reply.slots[-1][1] == start:
            reply.slots[-1][1] = end  # follows straight on from this reply's previous chunk
        else:
            reply.slots.append([start, end])
        self._queue_end = end

    def on_audio_done(self, item_id: str | None = None) -> None:
        """The model has finished an item's audio (without an id: every item so far)."""
        for key, reply in self._replies.items():
            if item_id is None or key == item_id:
                reply.complete = True

    def heard_ms(self, item_id: str) -> int:
        reply = self._replies.get(item_id)
        return int(reply.heard_ms(self._now)) if reply is not None else 0

    def cut_short(self) -> list[tuple[str, int]]:
        """Replies the caller has not heard in full, with how many ms of each they did hear.

        A reply the model is still producing always counts: more of it is on the way."""
        cut = []
        for item_id, reply in self._replies.items():
            heard = reply.heard_ms(self._now)
            if not reply.complete or heard < reply.sent_ms:
                cut.append((item_id, int(heard)))
        return cut

    def next_mark(self) -> str:
        name = f"m{next(self._counter)}"
        self._marks.add(name)
        return name

    def on_mark(self, name: str) -> None:
        self._marks.discard(name)
        if not self._marks:
            self._line_idle()

    def reset(self) -> None:
        """After a barge-in `clear`: Twilio has dropped everything that was still queued."""
        self._queue_end = None
        self._replies.clear()
        self._marks.clear()

    def _line_idle(self) -> None:
        # Twilio has played everything we sent: finished replies need no truncation, the
        # audio of unfinished ones has all been heard, and the next chunk plays on arrival.
        self._queue_end = None
        for item_id, reply in list(self._replies.items()):
            if reply.complete:
                del self._replies[item_id]
            else:
                reply.played_ms, reply.slots = reply.sent_ms, []
