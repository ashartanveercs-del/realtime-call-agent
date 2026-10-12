import asyncio
import xml.etree.ElementTree as ET

import pytest
from fakes import FakeConnector, is_type, start_event
from twilio.request_validator import RequestValidator
from twilio_stubs import StubTwilioClient

from callagent.config import Settings
from callagent.server import CONNECTOR, DEPS, MISSIONS, TOKENS, create_app
from callagent.twilio_gateway import TwilioGateway

OWNER = "+15550001111"
BASE = "https://agent.example.com"
AUTH = "auth-token"


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(openai_api_key="sk", twilio_account_sid="AC0", twilio_auth_token=AUTH,
                twilio_phone_number="+15550000000", public_base_url=BASE,
                owner_numbers=frozenset({OWNER}), api_token="api-secret", data_dir=tmp_path)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def twilio_client():
    return StubTwilioClient()


@pytest.fixture
async def client(tmp_path, aiohttp_client, twilio_client):
    settings = make_settings(tmp_path)
    gateway = TwilioGateway("AC0", AUTH, "+15550000000", client=twilio_client)
    app = create_app(settings, gateway=gateway, connector=FakeConnector())
    return await aiohttp_client(app)


def signed(path: str, params: dict) -> dict:
    return {"X-Twilio-Signature": RequestValidator(AUTH).compute_signature(BASE + path, params)}


def stream_token(xml: str) -> str:
    stream = ET.fromstring(xml).find("./Connect/Stream")
    assert stream.get("url") == "wss://agent.example.com/twilio/stream"
    return {p.get("name"): p.get("value") for p in stream.findall("Parameter")}["token"]


async def test_health_check(client):
    resp = await client.get("/healthz")
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True}


async def test_voice_webhook_rejects_unsigned_requests(client):
    resp = await client.post("/twilio/voice", data={"From": OWNER, "CallSid": "CA1"})
    assert resp.status == 403


async def test_voice_webhook_connects_the_owner_with_a_one_time_token(client):
    params = {"From": OWNER, "CallSid": "CA1"}
    resp = await client.post("/twilio/voice", data=params, headers=signed("/twilio/voice", params))

    assert resp.status == 200
    assert resp.content_type == "text/xml"
    token = stream_token(await resp.text())
    identity = client.app[TOKENS].claim(token)
    assert identity == {"role": "owner", "caller": OWNER, "call_sid": "CA1"}


async def test_unknown_callers_are_guests(client):
    params = {"From": "+15550002222", "CallSid": "CA2"}
    resp = await client.post("/twilio/voice", data=params, headers=signed("/twilio/voice", params))
    token = stream_token(await resp.text())
    assert client.app[TOKENS].claim(token)["role"] == "guest"


async def test_owner_attestation_can_be_required(tmp_path, aiohttp_client, twilio_client):
    settings = make_settings(tmp_path, owner_require_attestation=True)
    gateway = TwilioGateway("AC0", AUTH, "+15550000000", client=twilio_client)
    client = await aiohttp_client(create_app(settings, gateway=gateway, connector=FakeConnector()))

    spoofable = {"From": OWNER, "CallSid": "CA3", "StirVerstat": "TN-Validation-Passed-C"}
    resp = await client.post("/twilio/voice", data=spoofable,
                             headers=signed("/twilio/voice", spoofable))
    assert client.app[TOKENS].claim(stream_token(await resp.text()))["role"] == "guest"

    verified = {"From": OWNER, "CallSid": "CA4", "StirVerstat": "TN-Validation-Passed-A"}
    resp = await client.post("/twilio/voice", data=verified,
                             headers=signed("/twilio/voice", verified))
    assert client.app[TOKENS].claim(stream_token(await resp.text()))["role"] == "owner"


async def test_calls_api_requires_the_api_token(client):
    resp = await client.post("/calls", json={"to": "+15550003333", "goal": "Say hi"})
    assert resp.status == 401
    resp = await client.post("/calls", json={"to": "+15550003333", "goal": "Say hi"},
                             headers={"Authorization": "Bearer wrong"})
    assert resp.status == 401


async def test_calls_api_validates_input(client):
    headers = {"Authorization": "Bearer api-secret"}
    resp = await client.post("/calls", json={"to": "5550003333", "goal": "x"}, headers=headers)
    assert resp.status == 400
    resp = await client.post("/calls", json={"to": "+15550003333", "goal": ""}, headers=headers)
    assert resp.status == 400


async def test_calls_api_places_a_mission_call(client, twilio_client):
    headers = {"Authorization": "Bearer api-secret"}
    resp = await client.post("/calls", headers=headers,
                             json={"to": "+15550003333", "goal": "Confirm the delivery window."})

    assert resp.status == 201
    body = await resp.json()
    [created] = twilio_client.calls.created
    assert created["to"] == "+15550003333"
    token = stream_token(created["twiml"])
    identity = client.app[TOKENS].claim(token)
    assert identity["role"] == "mission"
    assert identity["mission_id"] == body["mission_id"]
    assert created["status_callback"] == f"{BASE}/twilio/status?mission={body['mission_id']}"

    resp = await client.get(f"/calls/{body['mission_id']}", headers=headers)
    assert (await resp.json())["status"] == "dialing"


async def test_status_callback_fails_an_unanswered_mission(client):
    headers = {"Authorization": "Bearer api-secret"}
    resp = await client.post("/calls", headers=headers,
                             json={"to": "+15550003333", "goal": "Say hi"})
    mission_id = (await resp.json())["mission_id"]

    path = f"/twilio/status?mission={mission_id}"
    params = {"CallSid": "CA_OUT_1", "CallStatus": "no-answer"}
    resp = await client.post(path, data=params, headers=signed(path, params))

    assert resp.status == 204
    mission = client.app[MISSIONS].get(mission_id)
    assert mission.status == "failed"
    assert "no-answer" in await mission.outcome()


async def test_status_callback_fails_a_mission_whose_call_ended_while_still_dialing(client):
    headers = {"Authorization": "Bearer api-secret"}
    resp = await client.post("/calls", headers=headers,
                             json={"to": "+15550003333", "goal": "Say hi"})
    mission_id = (await resp.json())["mission_id"]

    path = f"/twilio/status?mission={mission_id}"
    params = {"CallSid": "CA_OUT_1", "CallStatus": "completed"}
    resp = await client.post(path, data=params, headers=signed(path, params))

    assert resp.status == 204
    mission = client.app[MISSIONS].get(mission_id)
    assert mission.status == "failed"
    assert "before a conversation started" in await mission.outcome()


async def test_status_callback_leaves_a_connected_mission_to_its_call_session(client):
    headers = {"Authorization": "Bearer api-secret"}
    resp = await client.post("/calls", headers=headers,
                             json={"to": "+15550003333", "goal": "Say hi"})
    mission = client.app[MISSIONS].get((await resp.json())["mission_id"])
    mission.status = "in_progress"  # its media stream connected; that session reports back

    path = f"/twilio/status?mission={mission.id}"
    params = {"CallSid": "CA_OUT_1", "CallStatus": "completed"}
    resp = await client.post(path, data=params, headers=signed(path, params))

    assert resp.status == 204
    assert mission.status == "in_progress"
    assert mission.result is None


async def test_context_provider_is_handed_to_every_call_session(tmp_path, twilio_client):
    async def provider(*, role, caller):
        return ""

    gateway = TwilioGateway("AC0", AUTH, "+15550000000", client=twilio_client)
    app = create_app(make_settings(tmp_path), gateway=gateway, connector=FakeConnector(),
                     context_provider=provider)

    assert app[DEPS].services["context_provider"] is provider


async def test_stream_endpoint_runs_a_call_session(client):
    token = client.app[TOKENS].mint({"role": "owner", "caller": OWNER, "call_sid": "CA9"})
    ws = await client.ws_connect("/twilio/stream")
    await ws.send_json({"event": "connected"})
    await ws.send_json(start_event(token, call_sid="CA9"))

    model = await client.app[CONNECTOR].wait_model()
    update = await model.wait_for(is_type("session.update"))
    assert update["session"]["audio"]["output"]["format"] == {"type": "audio/pcmu"}

    await ws.send_json({"event": "stop"})
    await asyncio.wait_for(ws.receive(), 2)
    await ws.close()
