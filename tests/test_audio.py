from callagent.audio import PlayoutTracker, ulaw_ms

MS = 8  # mu-law bytes per millisecond of audio


def test_ulaw_8khz_is_eight_bytes_per_millisecond():
    assert ulaw_ms(8000) == 1000
    assert ulaw_ms(160) == 20


def test_heard_is_limited_by_elapsed_playback_time():
    t = PlayoutTracker()
    t.on_twilio_timestamp(1000)
    t.on_audio_sent("item1", 2000 * MS)  # 2 s of audio queued at once
    t.on_twilio_timestamp(1600)

    assert t.heard_ms("item1") == 600


def test_heard_is_limited_by_audio_actually_sent():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("item1", 100 * MS)
    t.on_twilio_timestamp(5000)

    assert t.heard_ms("item1") == 100


def test_without_twilio_clock_heard_falls_back_to_sent_audio():
    t = PlayoutTracker()
    t.on_audio_sent("item1", 200 * MS)
    assert t.heard_ms("item1") == 200


def test_a_reply_queued_behind_another_starts_when_the_first_one_ends():
    t = PlayoutTracker()
    t.on_twilio_timestamp(1000)
    t.on_audio_sent("A", 2000 * MS)  # plays 1000-3000
    t.on_twilio_timestamp(1100)
    t.on_audio_sent("B", 2000 * MS)  # waits behind A: plays 3000-5000
    t.on_twilio_timestamp(3500)

    assert t.heard_ms("A") == 2000
    assert t.heard_ms("B") == 500


def test_after_an_idle_gap_the_next_reply_starts_when_it_arrives():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("A", 100 * MS)
    t.on_twilio_timestamp(1000)
    t.on_audio_sent("B", 100 * MS)
    t.on_twilio_timestamp(1050)

    assert t.heard_ms("B") == 50


def test_a_gap_inside_a_reply_is_not_counted_as_heard():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("A", 100 * MS)  # plays 0-100, then the line runs dry
    t.on_twilio_timestamp(300)
    t.on_audio_sent("A", 100 * MS)  # plays 300-400
    t.on_twilio_timestamp(350)

    assert t.heard_ms("A") == 150


def test_cut_short_lists_every_reply_not_heard_in_full():
    t = PlayoutTracker()
    t.on_twilio_timestamp(1000)
    t.on_audio_sent("A", 2000 * MS)
    t.on_twilio_timestamp(1100)
    t.on_audio_sent("B", 2000 * MS)
    t.on_audio_done()  # the model has finished both replies

    t.on_twilio_timestamp(2500)
    assert t.cut_short() == [("A", 1500), ("B", 0)]
    t.on_twilio_timestamp(3500)
    assert t.cut_short() == [("B", 500)]
    t.on_twilio_timestamp(5000)
    assert t.cut_short() == []


def test_a_reply_still_streaming_counts_as_cut_short_even_if_all_sent_audio_played():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("A", 100 * MS)
    t.on_twilio_timestamp(500)

    assert t.cut_short() == [("A", 100)]  # more of A may still be on its way
    t.on_audio_done("A")
    assert t.cut_short() == []


def test_when_every_mark_is_back_finished_replies_are_forgotten():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("A", 100 * MS)
    mark = t.next_mark()
    t.on_audio_done("A")
    t.on_twilio_timestamp(80)  # the stream clock lags a little behind playback

    t.on_mark(mark)  # Twilio: everything sent has played

    assert t.pending_marks == 0
    assert t.cut_short() == []
    t.on_audio_sent("B", 100 * MS)  # the line is idle, so B starts on arrival
    t.on_twilio_timestamp(130)
    assert t.heard_ms("B") == 50


def test_unknown_items_have_not_been_heard():
    assert PlayoutTracker().heard_ms("nope") == 0


def test_reset_forgets_queued_replies():
    t = PlayoutTracker()
    t.on_twilio_timestamp(0)
    t.on_audio_sent("item1", 2000 * MS)
    t.reset()

    assert t.cut_short() == []
    assert t.heard_ms("item1") == 0
    t.on_twilio_timestamp(100)
    t.on_audio_sent("item2", 100 * MS)  # nothing is queued ahead of it any more
    t.on_twilio_timestamp(130)
    assert t.heard_ms("item2") == 30


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
