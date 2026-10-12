import json

from callagent import realtime as rt


def test_session_update_uses_nested_ga_audio_config_with_mulaw_passthrough():
    tools = [{"type": "function", "name": "end_call", "description": "d", "parameters": {}}]
    ev = rt.session_update(instructions="Be brief.", tools=tools, voice="marin")

    session = ev["session"]
    assert ev["type"] == "session.update"
    assert session["type"] == "realtime"
    assert session["output_modalities"] == ["audio"]
    assert session["instructions"] == "Be brief."
    assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["output"]["voice"] == "marin"
    assert session["audio"]["input"]["turn_detection"]["type"] == "server_vad"
    assert session["tools"] == tools
    assert session["tool_choice"] == "auto"


def test_session_update_can_enable_input_transcription():
    ev = rt.session_update(instructions="x", tools=[], voice="cedar",
                           transcription_model="whisper-1")
    assert ev["session"]["audio"]["input"]["transcription"] == {"model": "whisper-1"}


def test_tool_output_wraps_result_as_function_call_output_item():
    ev = rt.tool_output("call_9", {"ok": True})
    assert ev == {
        "type": "conversation.item.create",
        "item": {"type": "function_call_output", "call_id": "call_9",
                 "output": json.dumps({"ok": True})},
    }


def test_truncate_clamps_negative_positions_to_zero():
    assert rt.truncate("item_1", -50) == {
        "type": "conversation.item.truncate",
        "item_id": "item_1", "content_index": 0, "audio_end_ms": 0,
    }


def test_system_note_and_create_response():
    note = rt.system_note("You were interrupted.")
    assert note["item"]["role"] == "system"
    assert note["item"]["content"][0] == {"type": "input_text", "text": "You were interrupted."}
    assert rt.create_response() == {"type": "response.create"}
    assert rt.create_response("Say hi")["response"]["instructions"] == "Say hi"


def test_usage_accumulates_tokens_from_response_done():
    usage = rt.Usage()
    done = {"type": "response.done", "response": {"usage": {
        "input_token_details": {"text_tokens": 100, "audio_tokens": 400, "cached_tokens": 50},
        "output_token_details": {"text_tokens": 20, "audio_tokens": 300},
    }}}
    usage.add_response_done(done)
    usage.add_response_done(done)

    assert usage.as_dict() == {"input_text": 200, "input_audio": 800, "cached": 100,
                               "output_text": 40, "output_audio": 600}


def test_usage_cost_uses_prices_per_million_tokens():
    usage = rt.Usage(input_audio=1_000_000, output_audio=500_000)
    cost = usage.cost({"input_audio": 10.0, "output_audio": 20.0})
    assert cost == 20.0
    assert rt.Usage().cost({}) is None
