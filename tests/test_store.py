import json

from callagent.store import CallStore


def test_log_appends_json_lines_per_call(tmp_path):
    store = CallStore(tmp_path)
    store.log("CA123", "turn", role="caller", text="hello")
    store.log("CA123", "turn", role="assistant", text="hi there")

    events = store.read("CA123")
    assert [(e["kind"], e["role"], e["text"]) for e in events] == [
        ("turn", "caller", "hello"), ("turn", "assistant", "hi there")]
    assert all("ts" in e for e in events)


def test_call_sid_cannot_escape_the_data_directory(tmp_path):
    store = CallStore(tmp_path / "data")
    store.log("../../evil", "turn", text="x")

    written = list((tmp_path / "data").rglob("*.jsonl"))
    assert len(written) == 1
    assert not (tmp_path / "evil.jsonl").exists()


def test_messages_round_trip(tmp_path):
    store = CallStore(tmp_path)
    store.add_message(caller="+15550002222", text="Please call back about the invoice.",
                      call_sid="CA9")

    [msg] = store.messages()
    assert msg["caller"] == "+15550002222"
    assert msg["text"] == "Please call back about the invoice."


def test_summary_is_saved_as_json(tmp_path):
    store = CallStore(tmp_path)
    path = store.save_summary("CA1", {"duration_s": 42})
    assert json.loads(path.read_text(encoding="utf-8")) == {"duration_s": 42}
