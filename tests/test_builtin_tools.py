import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from twilio_stubs import RecordingGateway

from callagent.builtin_tools import build_registry, load_contacts, resolve_number
from callagent.config import Settings
from callagent.missions import MissionRegistry
from callagent.store import CallStore
from callagent.tokens import TokenStore
from callagent.tools import ToolContext, ToolResult

OWNER = "+15550001111"


def settings(tmp_path, **overrides) -> Settings:
    base = dict(openai_api_key="sk", twilio_account_sid="AC0", twilio_auth_token="tok",
                twilio_phone_number="+15550000000", public_base_url="https://agent.example.com",
                owner_name="Sam", owner_numbers=frozenset({OWNER}), data_dir=tmp_path)
    base.update(overrides)
    return Settings(**base)


def context(tmp_path, *, role="owner", caller=OWNER, services=None, **overrides):
    hung_up = []
    ctx = ToolContext(call_sid="CA1", caller=caller, role=role,
                      settings=settings(tmp_path, **overrides), store=CallStore(tmp_path),
                      services=services or {}, hangup=lambda: hung_up.append(True))
    return ctx, hung_up


def test_contacts_resolve_names_and_numbers(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text('{"Alex Chen": "+1 555 000 4444"}', encoding="utf-8")
    contacts = load_contacts(path)

    assert resolve_number("alex chen", contacts) == "+15550004444"
    assert resolve_number("+1 (555) 000-5555", contacts) == "+15550005555"
    assert resolve_number("Unknown Person", contacts) is None
    assert resolve_number("12345", contacts) is None


async def test_end_call_requests_hangup_without_a_follow_up_response(tmp_path):
    reg = build_registry(settings(tmp_path))
    ctx, hung_up = context(tmp_path)

    result = await reg.get("end_call", "owner").handler(ctx)

    assert hung_up == [True]
    assert isinstance(result, ToolResult) and result.respond is False


async def test_take_message_saves_it_and_texts_the_owner(tmp_path):
    gateway = RecordingGateway()
    reg = build_registry(settings(tmp_path))
    ctx, _ = context(tmp_path, role="guest", caller="+15550002222",
                     services={"gateway": gateway})

    result = await reg.get("take_message", "guest").handler(
        ctx, message="Please call back about the invoice.", caller_name="Jordan")

    assert result["ok"] is True
    [msg] = ctx.store.messages()
    assert msg["text"] == "Please call back about the invoice."
    assert gateway.texts[0]["to"] == OWNER
    assert "Jordan" in gateway.texts[0]["body"]


async def test_current_time_reads_naturally(tmp_path):
    reg = build_registry(settings(tmp_path, timezone="UTC"))
    fixed = datetime(2026, 10, 12, 15, 5, tzinfo=ZoneInfo("UTC"))
    ctx, _ = context(tmp_path, services={"now": lambda tz: fixed}, timezone="UTC")

    text = await reg.get("current_time", "guest").handler(ctx)

    assert text == "It's 3:05 PM on Monday, October 12."


async def test_send_sms_resolves_a_contact_name(tmp_path):
    gateway = RecordingGateway()
    reg = build_registry(settings(tmp_path))
    ctx, _ = context(tmp_path, services={"gateway": gateway,
                                         "contacts": {"alex": "+15550004444"}})

    result = await reg.get("send_sms", "owner").handler(ctx, to="Alex", body="Running late")

    assert result == {"ok": True, "to": "+15550004444"}
    assert gateway.texts == [{"to": "+15550004444", "body": "Running late"}]


async def test_send_sms_refuses_an_unknown_recipient(tmp_path):
    gateway = RecordingGateway()
    reg = build_registry(settings(tmp_path))
    ctx, _ = context(tmp_path, services={"gateway": gateway, "contacts": {}})

    result = await reg.get("send_sms", "owner").handler(ctx, to="Nobody", body="hi")

    assert result["ok"] is False
    assert gateway.texts == []


async def test_call_person_places_a_mission_call_and_reports_what_was_said(tmp_path):
    gateway, missions, tokens = RecordingGateway(), MissionRegistry(), TokenStore()
    reg = build_registry(settings(tmp_path))
    ctx, _ = context(tmp_path, services={"gateway": gateway, "missions": missions,
                                         "tokens": tokens, "contacts": {}})
    tool = reg.get("call_person", "owner")
    assert tool.background is True

    task = asyncio.create_task(tool.handler(ctx, who="+15550003333",
                                            goal="Confirm Friday's delivery window."))
    while not gateway.calls:
        await asyncio.sleep(0.01)
    [mission] = list(missions._missions.values())
    assert gateway.calls[0]["to"] == "+15550003333"
    assert "/twilio/status?mission=" + mission.id in gateway.calls[0]["status_callback"]

    missions.finish(mission.id, "Them: Nine to eleven works.")
    result = await asyncio.wait_for(task, 1)

    assert "Nine to eleven works." in result
    assert tool.describe({"who": "+15550003333"}) == "calling +15550003333"


async def test_delegate_task_asks_the_configured_agent(tmp_path, aiohttp_server):
    seen = []

    async def agent(request):
        seen.append(await request.json())
        return web.json_response({"answer": "Your next meeting is at 4 PM."})

    app = web.Application()
    app.router.add_post("/agent", agent)
    server = await aiohttp_server(app)
    url = str(server.make_url("/agent"))

    reg = build_registry(settings(tmp_path, agent_webhook_url=url))
    async with aiohttp.ClientSession() as http:
        ctx, _ = context(tmp_path, services={"http": http}, agent_webhook_url=url)
        tool = reg.get("delegate_task", "owner")
        answer = await tool.handler(ctx, task="When's my next meeting?")

    assert answer == "Your next meeting is at 4 PM."
    assert seen == [{"task": "When's my next meeting?", "caller": OWNER, "call_sid": "CA1"}]


def test_delegate_task_only_exists_when_an_agent_is_configured(tmp_path):
    assert build_registry(settings(tmp_path)).get("delegate_task", "owner") is None


def test_guests_cannot_see_owner_tools(tmp_path):
    names = build_registry(settings(tmp_path)).names("guest")
    assert "send_sms" not in names
    assert "call_person" not in names
    assert "take_message" in names
