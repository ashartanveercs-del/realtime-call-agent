"""Tracks what the caller has actually *heard*, which is what barge-in truncation needs.

The model streams audio much faster than real time, so "bytes sent to Twilio" overstates
what was played. Twilio's inbound media frames carry a stream-clock timestamp (ms since the
stream started); the time elapsed on that clock since an item's first audio chunk bounds
how much of it can have been played. Heard = min(audio sent, playback time elapsed).

Marks: after each audio chunk we send a Twilio `mark`; Twilio echoes it back once the audio
before it has finished playing. Pending marks == audio still queued on the caller's line.
"""

from __future__ import annotations

import itertools

ULAW_BYTES_PER_MS = 8  # G.711 mu-law, 8 kHz, 8-bit mono


def ulaw_ms(nbytes: int) -> int:
    return nbytes // ULAW_BYTES_PER_MS


class PlayoutTracker:
    def __init__(self) -> None:
        self._latest_ts: int | None = None
        self._item: str | None = None
        self._item_start_ts: int | None = None
        self._sent_ms = 0
        self._marks: set[str] = set()
        self._counter = itertools.count(1)

    @property
    def current_item(self) -> str | None:
        return self._item

    @property
    def pending_marks(self) -> int:
        return len(self._marks)

    def on_twilio_timestamp(self, ts_ms: int) -> None:
        self._latest_ts = ts_ms

    def on_audio_sent(self, item_id: str, nbytes: int) -> None:
        if item_id != self._item:
            self._item = item_id
            self._item_start_ts = self._latest_ts
            self._sent_ms = 0
        self._sent_ms += ulaw_ms(nbytes)

    def heard_ms(self) -> int:
        if self._item is None:
            return 0
        if self._latest_ts is None or self._item_start_ts is None:
            return self._sent_ms
        elapsed = max(0, self._latest_ts - self._item_start_ts)
        return min(self._sent_ms, elapsed)

    def next_mark(self) -> str:
        name = f"m{next(self._counter)}"
        self._marks.add(name)
        return name

    def on_mark(self, name: str) -> None:
        self._marks.discard(name)

    def reset(self) -> None:
        self._item = None
        self._item_start_ts = None
        self._sent_ms = 0
        self._marks.clear()
