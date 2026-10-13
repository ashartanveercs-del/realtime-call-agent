"""Demonstrate the real playout tracker without a phone, API key or network.

Run after installing the project: python examples/barge_in_demo.py
"""

from callagent.audio import ULAW_BYTES_PER_MS, PlayoutTracker


def main() -> None:
    tracker = PlayoutTracker()
    tracker.on_twilio_timestamp(1000)
    tracker.on_audio_sent("answer-A", 2000 * ULAW_BYTES_PER_MS)
    tracker.on_twilio_timestamp(1100)
    tracker.on_audio_sent("answer-B", 2000 * ULAW_BYTES_PER_MS)
    tracker.on_audio_done()

    print("Two 2-second answers queued; B waits for A to finish.")
    print("A plays at 1000-3000 ms. B plays at 3000-5000 ms.\n")
    print("Caller interrupts at | A heard | B heard | Truncate these items")
    for timestamp, expected in (
        (1600, [("answer-A", 600), ("answer-B", 0)]),
        (3500, [("answer-B", 500)]),
        (5000, []),
    ):
        tracker.on_twilio_timestamp(timestamp)
        cut = tracker.cut_short()
        if cut != expected:
            raise RuntimeError(f"Incorrect truncation at {timestamp} ms: {cut!r}")
        print(f"{timestamp:>20} | {tracker.heard_ms('answer-A'):>7} | "
              f"{tracker.heard_ms('answer-B'):>7} | {cut}")

    tracker.reset()
    if tracker.cut_short() or tracker.pending_marks:
        raise RuntimeError("Barge-in did not clear the playout state")
    print("\nAfter clear: no queued audio remains.")
    print("Verified: truncate to audio heard, including answers still waiting in the queue.")


if __name__ == "__main__":
    main()
