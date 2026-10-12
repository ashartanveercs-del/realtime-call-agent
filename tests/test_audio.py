from callagent.audio import PlayoutTracker, ulaw_ms


def test_ulaw_8khz_is_eight_bytes_per_millisecond():
    assert ulaw_ms(8000) == 1000
    assert ulaw_ms(160) == 20


def test_heard_is_limited_by_elapsed_playback_time():
    t = PlayoutTracker()
    t.on_twilio_timestamp(1000)
    t.on_audio_sent("item1", 16000)  # 2000 ms of audio queued at once
    t.on_twilio_timestamp(1600)

    assert t.current_item == "item1"
    assert t.heard_ms() == 600


def test_heard_is_limited_by_audio_actually_sent():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("item1", 800)  # 100 ms
    t.on_twilio_timestamp(5000)

    assert t.heard_ms() == 100


def test_without_twilio_clock_heard_falls_back_to_sent_audio():
    t = PlayoutTracker()
    t.on_audio_sent("item1", 1600)
    assert t.heard_ms() == 200


def test_new_item_restarts_the_count():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("item1", 8000)
    t.on_twilio_timestamp(900)
    t.on_audio_sent("item2", 800)
    t.on_twilio_timestamp(1000)

    assert t.current_item == "item2"
    assert t.heard_ms() == 100


def test_reset_forgets_the_current_item():
    t = PlayoutTracker()
    t.on_audio_sent("item1", 800)
    t.reset()
    assert t.current_item is None
    assert t.heard_ms() == 0


def test_marks_track_audio_still_queued_at_twilio():
    t = PlayoutTracker()
    first = t.next_mark()
    second = t.next_mark()
    assert first != second
    assert t.pending_marks == 2

    t.on_mark(first)
    assert t.pending_marks == 1
    t.on_mark("unknown-mark")
    assert t.pending_marks == 1
    t.on_mark(second)
    assert t.pending_marks == 0


def test_reset_drops_pending_marks():
    t = PlayoutTracker()
    t.next_mark()
    t.reset()
    assert t.pending_marks == 0
