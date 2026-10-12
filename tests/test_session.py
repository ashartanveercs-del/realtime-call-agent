import asyncio
import json
from dataclasses import dataclass

from fakes import FakeConnector, FakeSocket, audio_delta, is_type, media_event, start_event

from callagent.config import Settings
from callagent.missions import MissionRegistry
from callagent.session import CallSession, SessionDeps
from callagent.store import CallStore
from callagent.tokens import TokenStore
from callagent.tools import ToolRegistry, ToolResult

OWNER = "+15550001111"
GUEST = "+15550002222"


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(openai_api_key="sk-test", twilio_account_sid="AC0", twilio_auth_token="tok",
                twilio_phone_number="+15550000000", public_base_url="https://agent.example.com",
                agent_name="Robin", owner_name="Sam", owner_numbers=frozenset({OWNER}),
                data_dir=tmp_path, silence_nudge_seconds=60, silence_end_seconds=120)
    base.update(overrides)
    return Settings(**base)


def basic_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(description="Hang up.", roles=("owner", "guest", "mission"))
    async def end_call(ctx):
        ctx.hangup()
        return ToolResult({"ok": True}, respond=False)

    @reg.tool(description="Send a text.", roles=("owner",),
              parameters={"type": "object", "properties": {"to": {"type": "string"},
                                                           "body": {"type": "string"}}})
    async def send_sms(ctx, to, body):
        return {"ok": True, "sent_to": to}

    @reg.tool(description="Take a message.", roles=("guest",))
    async def take_message(ctx):
        return {"ok": True}

    return reg


@dataclass
class Call:
    session: CallSession
    twilio: FakeSocket
    model: FakeSocket
    task: asyncio.Task
    deps: SessionDeps


async def start_call(tmp_path, *, role="owner", registry=None, auto_ack=True,
                     **settings_overrides) -> Call:
    settings = make_settings(tmp_path, **settings_overrides)
    tokens, missions = TokenStore(), MissionRegistry()
    payload = {"role": role, "caller": OWNER if role == "owner" else GUEST, "call_sid": "CA1"}
    if role == "mission":
        mission = missions.create(to=GUEST, goal="Confirm the delivery window.",
                                  requested_by=OWNER)
        payload["mission_id"] = mission.id
    connector = FakeConnector(auto_ack_responses=auto_ack)
    deps = SessionDeps(settings=settings, tokens=tokens, connector=connector,
                       registry=registry or basic_registry(), store=CallStore(tmp_path),
                       missions=missions)
    twilio = FakeSocket()
    session = CallSession(twilio, deps, tick_seconds=0.01, hangup_grace=1.0)
    task = asyncio.create_task(session.run())
    twilio.push({"event": "connected"})
    twilio.push(start_event(tokens.mint(payload)))
    model = await connector.wait_model()
    await model.wait_for(is_type("response.create"))  # greeting
    if not auto_ack:
        model.push({"type": "response.created"})
        model.push({"type": "response.done", "response": {}})
    await asyncio.sleep(0.02)
    return Call(session, twilio, model, task, deps)


async def finish(call: Call) -> None:
    call.twilio.push({"event": "stop"})
    await asyncio.wait_for(call.task, 2)


def tool_outputs(model: FakeSocket, call_id: str) -> list[dict]:
    return [json.loads(m["item"]["output"]) for m in model.sent
            if m.get("type") == "conversation.item.create"
            and m["item"].get("type") == "function_call_output"
            and m["item"].get("call_id") == call_id]


def function_call(name: str, call_id: str, **args) -> dict:
    return {"type": "response.function_call_arguments.done", "name": name,
            "call_id": call_id, "arguments": json.dumps(args)}


# --- setup -------------------------------------------------------------------------------

async def test_owner_call_configures_the_model_with_owner_persona_and_tools(tmp_path):
    call = await start_call(tmp_path, role="owner")

    update = call.model.sent[0]
    assert update["type"] == "session.update"
    session = update["session"]
    assert "Sam" in session["instructions"]
    assert [t["name"] for t in session["tools"]] == ["end_call", "send_sms"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    await finish(call)


async def test_guest_only_gets_guest_tools(tmp_path):
    call = await start_call(tmp_path, role="guest")
    tools = [t["name"] for t in call.model.sent[0]["session"]["tools"]]
    assert tools == ["end_call", "take_message"]
    await finish(call)


async def test_stream_without_a_valid_token_is_hung_up_before_any_model_session(tmp_path):
    settings = make_settings(tmp_path)
    connector = FakeConnector()
    deps = SessionDeps(settings=settings, tokens=TokenStore(), connector=connector,
                       registry=basic_registry(), store=CallStore(tmp_path),
                       missions=MissionRegistry())
    twilio = FakeSocket()
    task = asyncio.create_task(CallSession(twilio, deps, tick_seconds=0.01).run())
    twilio.push(start_event("forged-token"))

    await asyncio.wait_for(task, 2)
    assert twilio.closed
    assert connector.sockets == []


# --- audio relay ---------------------------------------------------------------------------

async def test_caller_audio_is_relayed_to_the_model(tmp_path):
    call = await start_call(tmp_path)
    call.twilio.push(media_event(payload="AAEC", ts=20))

    msg = await call.model.wait_for(is_type("input_audio_buffer.append"))
    assert msg["audio"] == "AAEC"
    await finish(call)


async def test_model_audio_is_played_to_the_caller_followed_by_a_mark(tmp_path):
    call = await start_call(tmp_path)
    call.model.push(audio_delta("item1", 160))

    media = await call.twilio.wait_for(is_type("media"))
    assert media["streamSid"] == "MZ1"
    mark = await call.twilio.wait_for(is_type("mark"))
    assert call.twilio.sent.index(mark) > call.twilio.sent.index(media)
    await finish(call)


# --- barge-in ------------------------------------------------------------------------------

async def _speaking(call: Call, nbytes: int, played_ms: int) -> None:
    call.twilio.push(media_event(ts=1000))
    await call.model.wait_for(is_type("input_audio_buffer.append"))
    call.model.push({"type": "response.created"})
    call.model.push(audio_delta("answer", nbytes))
    await call.twilio.wait_for(is_type("media"))
    before = len(call.model.sent)
    call.twilio.push(media_event(ts=1000 + played_ms))
    await call.model.wait_for(is_type("input_audio_buffer.append"), after=before)


async def test_barge_in_flushes_audio_cancels_and_truncates_to_what_was_heard(tmp_path):
    call = await start_call(tmp_path, auto_ack=False)
    await _speaking(call, nbytes=16000, played_ms=600)  # 2 s queued, 0.6 s played

    call.model.push({"type": "input_audio_buffer.speech_started"})

    await call.twilio.wait_for(is_type("clear"))
    await call.model.wait_for(is_type("response.cancel"))
    trunc = await call.model.wait_for(is_type("conversation.item.truncate"))
    assert trunc["item_id"] == "answer"
    assert trunc["audio_end_ms"] == 600
    await finish(call)


async def test_long_interruption_tells_the_model_it_was_cut_off(tmp_path):
    call = await start_call(tmp_path, auto_ack=False)
    await _speaking(call, nbytes=40000, played_ms=2500)

    call.model.push({"type": "input_audio_buffer.speech_started"})

    note = await call.model.wait_for(
        lambda m: m.get("type") == "conversation.item.create"
        and m["item"].get("role") == "system")
    assert "interrupted" in note["item"]["content"][0]["text"].lower()
    await finish(call)


async def test_speech_after_playback_finished_does_not_truncate(tmp_path):
    call = await start_call(tmp_path, auto_ack=False)
    call.model.push({"type": "response.created"})
    call.model.push(audio_delta("answer", 800))
    mark = await call.twilio.wait_for(is_type("mark"))
    call.model.push({"type": "response.done", "response": {}})
    call.twilio.push({"event": "mark", "streamSid": "MZ1", "mark": {"name": mark["mark"]["name"]}})
    await asyncio.sleep(0.05)

    call.model.push({"type": "input_audio_buffer.speech_started"})
    await asyncio.sleep(0.05)

    assert call.model.matching(is_type("conversation.item.truncate")) == []
    assert call.model.matching(is_type("response.cancel")) == []
    assert call.twilio.matching(is_type("clear")) == []
    await finish(call)


# --- tools -----------------------------------------------------------------------------------

async def test_fast_tool_result_goes_back_and_the_model_speaks_next(tmp_path):
    call = await start_call(tmp_path)
    responses_before = len(call.model.matching(is_type("response.create")))

    call.model.push(function_call("send_sms", "c1", to="+15550003333", body="Running late"))

    await call.model.wait_for(lambda m: tool_outputs(call.model, "c1") != [])
    assert tool_outputs(call.model, "c1") == [{"ok": True, "sent_to": "+15550003333"}]
    await call.model.wait_for(
        lambda m: len(call.model.matching(is_type("response.create"))) > responses_before)
    await finish(call)


async def test_tool_outside_the_callers_role_is_refused_without_running(tmp_path):
    ran = []
    reg = basic_registry()

    @reg.tool(description="Read the owner's inbox.", roles=("owner",))
    async def read_inbox(ctx):
        ran.append(True)
        return "secrets"

    call = await start_call(tmp_path, role="guest", registry=reg)
    call.model.push(function_call("read_inbox", "c9"))

    await call.model.wait_for(lambda m: tool_outputs(call.model, "c9") != [])
    assert tool_outputs(call.model, "c9")[0]["ok"] is False
    assert ran == []
    await finish(call)


def background_registry(gate: asyncio.Event) -> ToolRegistry:
    reg = basic_registry()

    @reg.tool(description="Find flights.", background=True, timeout=5,
              parameters={"type": "object", "properties": {"city": {"type": "string"}}},
              label=lambda a: f"flights to {a['city']}")
    async def find_flights(ctx, city):
        await gate.wait()
        return f"Two morning flights to {city} tomorrow."

    return reg


async def test_slow_tool_runs_in_background_and_its_result_is_spoken_when_ready(tmp_path):
    gate = asyncio.Event()
    call = await start_call(tmp_path, registry=background_registry(gate))

    call.model.push(function_call("find_flights", "c2", city="Lisbon"))
    await call.model.wait_for(lambda m: tool_outputs(call.model, "c2") != [])
    assert tool_outputs(call.model, "c2")[0]["status"] == "started"

    gate.set()
    note = await call.model.wait_for(
        lambda m: m.get("type") == "conversation.item.create"
        and m["item"].get("role") == "system"
        and "Lisbon" in m["item"]["content"][0]["text"])
    idx = call.model.sent.index(note)
    await call.model.wait_for(is_type("response.create"), after=idx)
    await finish(call)


async def test_background_result_waits_until_the_model_stops_speaking(tmp_path):
    gate = asyncio.Event()
    call = await start_call(tmp_path, registry=background_registry(gate), auto_ack=False)
    call.model.push(function_call("find_flights", "c3", city="Rome"))
    await call.model.wait_for(lambda m: tool_outputs(call.model, "c3") != [])
    call.model.push({"type": "response.created"})
    call.model.push({"type": "response.done", "response": {}})
    call.model.push({"type": "response.created"})  # model is talking again
    await asyncio.sleep(0.03)

    gate.set()
    await asyncio.sleep(0.1)
    assert not any("Rome" in m["item"]["content"][0]["text"] for m in call.model.sent
                   if m.get("type") == "conversation.item.create"
                   and m["item"].get("role") == "system")

    call.model.push({"type": "response.done", "response": {}})
    await call.model.wait_for(
        lambda m: m.get("type") == "conversation.item.create"
        and m["item"].get("role") == "system" and "Rome" in m["item"]["content"][0]["text"])
    await finish(call)


# --- ending calls ------------------------------------------------------------------------------

async def test_end_call_hangs_up_only_after_the_goodbye_has_played(tmp_path):
    call = await start_call(tmp_path, auto_ack=False)
    call.model.push({"type": "response.created"})
    call.model.push(audio_delta("bye", 800))
    mark = await call.twilio.wait_for(is_type("mark"))
    call.model.push(function_call("end_call", "c4"))
    call.model.push({"type": "response.done", "response": {}})
    await call.model.wait_for(lambda m: tool_outputs(call.model, "c4") != [])
    await asyncio.sleep(0.1)
    assert not call.twilio.closed

    call.twilio.push({"event": "mark", "streamSid": "MZ1", "mark": {"name": mark["mark"]["name"]}})

    await call.twilio.wait_closed()
    await asyncio.wait_for(call.task, 2)
    assert (tmp_path / "summaries" / "CA1.json").exists()


async def test_silence_gets_a_nudge_then_a_goodbye(tmp_path):
    call = await start_call(tmp_path, silence_nudge_seconds=0.15, silence_end_seconds=0.45)

    nudge = await call.model.wait_for(
        lambda m: m.get("type") == "response.create"
        and "still there" in m.get("response", {}).get("instructions", "").lower())
    await call.model.wait_for(
        lambda m: m.get("type") == "response.create"
        and "goodbye" in m.get("response", {}).get("instructions", "").lower(),
        after=call.model.sent.index(nudge) + 1)
    await call.twilio.wait_closed()
    await asyncio.wait_for(call.task, 2)


async def test_max_call_duration_wraps_up_politely(tmp_path):
    call = await start_call(tmp_path, max_call_seconds=0.2)

    await call.model.wait_for(
        lambda m: m.get("type") == "response.create"
        and "wrap up" in m.get("response", {}).get("instructions", "").lower())
    await call.twilio.wait_closed()
    await asyncio.wait_for(call.task, 2)


async def test_transcript_and_usage_are_saved_in_the_call_summary(tmp_path):
    call = await start_call(tmp_path)
    call.model.push({"type": "conversation.item.input_audio_transcription.completed",
                     "transcript": "Can you text Alex that I'm running late?"})
    call.model.push({"type": "response.done", "response": {"usage": {
        "input_token_details": {"audio_tokens": 120, "text_tokens": 900},
        "output_token_details": {"audio_tokens": 300, "text_tokens": 40}}}})
    await asyncio.sleep(0.05)
    await finish(call)

    summary = json.loads((tmp_path / "summaries" / "CA1.json").read_text())
    assert summary["role"] == "owner"
    assert summary["usage"]["input_audio"] == 120
    assert summary["usage"]["output_audio"] == 300
    turns = [e for e in call.deps.store.read("CA1") if e["kind"] == "turn"]
    assert turns[0]["text"] == "Can you text Alex that I'm running late?"


async def test_mission_call_reports_its_conversation_as_the_outcome(tmp_path):
    call = await start_call(tmp_path, role="mission")
    instructions = call.model.sent[0]["session"]["instructions"]
    assert "Confirm the delivery window." in instructions
    assert "AI assistant" in instructions

    call.model.push({"type": "conversation.item.input_audio_transcription.completed",
                     "transcript": "Nine to eleven on Friday works."})
    await asyncio.sleep(0.05)
    await finish(call)

    [mission] = list(call.deps.missions._missions.values())
    assert mission.status == "completed"
    assert "Nine to eleven" in await mission.outcome()
