"""HTTP + WebSocket entry points.

POST /twilio/voice    incoming-call webhook (signature-checked) -> TwiML <Connect><Stream>
GET  /twilio/stream   Twilio Media Stream WebSocket -> one CallSession per call
POST /twilio/status   outbound-call status callback (signature-checked)
POST /calls           start an outbound "mission" call (Bearer API_TOKEN)
GET  /calls/{id}      mission status and outcome (Bearer API_TOKEN)
GET  /healthz
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import web

from .builtin_tools import build_registry, load_contacts
from .config import Settings, is_e164, normalise_number
from .missions import Mission, MissionRegistry
from .outbound import place_mission_call
from .realtime import OpenAIRealtimeConnector, RealtimeConnector
from .session import CallSession, SessionDeps
from .sockets import AiohttpJsonSocket
from .store import CallStore
from .tokens import TokenStore
from .tools import ToolRegistry
from .twilio_gateway import TwilioGateway
from .twiml import connect_stream

log = logging.getLogger(__name__)

TOKENS = web.AppKey("tokens", TokenStore)
MISSIONS = web.AppKey("missions", MissionRegistry)
CONNECTOR: web.AppKey[Any] = web.AppKey("connector")
DEPS = web.AppKey("deps", SessionDeps)

UNANSWERED = {"busy", "no-answer", "failed", "canceled"}
ATTESTED = "TN-Validation-Passed-A"


def create_app(settings: Settings, *, gateway: Any | None = None,
               connector: RealtimeConnector | None = None,
               registry: ToolRegistry | None = None, store: CallStore | None = None,
               context_provider: Callable[..., Awaitable[str]] | None = None) -> web.Application:
    tokens = TokenStore(ttl_seconds=300)  # outbound calls may ring for a while
    missions = MissionRegistry()
    store = store or CallStore(settings.data_dir)
    gateway = gateway or TwilioGateway(settings.twilio_account_sid, settings.twilio_auth_token,
                                       settings.twilio_phone_number)
    connector = connector or OpenAIRealtimeConnector(
        settings.openai_api_key, settings.realtime_model, settings.realtime_url)
    services: dict[str, Any] = {"gateway": gateway, "missions": missions, "tokens": tokens,
                                "contacts": load_contacts(settings.contacts_file)}
    if context_provider is not None:
        services["context_provider"] = context_provider
    deps = SessionDeps(settings=settings, tokens=tokens, connector=connector,
                       registry=registry or build_registry(settings), store=store,
                       missions=missions, services=services)

    async def verified_form(request: web.Request) -> dict[str, str] | None:
        form = {key: str(value) for key, value in (await request.post()).items()}
        if not settings.validate_signatures:
            return form
        url = settings.public_base_url + request.path_qs
        if gateway.is_valid_request(url, form, request.headers.get("X-Twilio-Signature", "")):
            return form
        log.warning("rejected unsigned or forged Twilio request to %s", request.path)
        return None

    def authorized(request: web.Request) -> bool:
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        expected = settings.api_token or ""
        return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())

    def mission_json(mission: Mission) -> dict[str, Any]:
        return {"mission_id": mission.id, "to": mission.to, "goal": mission.goal,
                "status": mission.status, "outcome": mission.result}

    async def voice(request: web.Request) -> web.Response:
        form = await verified_form(request)
        if form is None:
            return web.Response(status=403, text="invalid signature")
        caller = normalise_number(form.get("From", ""))
        is_owner = settings.is_owner(caller) and (
            not settings.owner_require_attestation or form.get("StirVerstat") == ATTESTED)
        token = tokens.mint({"role": "owner" if is_owner else "guest", "caller": caller,
                             "call_sid": form.get("CallSid", "")})
        return web.Response(text=connect_stream(settings.stream_url, {"token": token}),
                            content_type="text/xml")

    async def status_callback(request: web.Request) -> web.Response:
        form = await verified_form(request)
        if form is None:
            return web.Response(status=403, text="invalid signature")
        mission = missions.get(request.query.get("mission", ""))
        call_status = form.get("CallStatus", "")
        if mission is not None:
            if call_status in UNANSWERED:
                missions.fail(mission.id, f"The call to {mission.to} was not answered "
                                          f"({call_status}).")
            elif call_status == "completed" and mission.status == "dialing":
                missions.fail(mission.id, "The call ended before a conversation started.")
        return web.Response(status=204)

    async def stream(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await CallSession(AiohttpJsonSocket(ws), deps).run()
        return ws

    async def create_call(request: web.Request) -> web.Response:
        if not settings.api_token:
            raise web.HTTPNotFound()
        if not authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "body must be JSON"}, status=400)
        to = normalise_number(str(body.get("to", "")))
        goal = str(body.get("goal", "")).strip()
        if not is_e164(to):
            return web.json_response({"error": "'to' must be an E.164 number, e.g. "
                                               "+15551234567"}, status=400)
        if not goal or len(goal) > 1000:
            return web.json_response({"error": "'goal' is required (max 1000 chars)"},
                                     status=400)
        mission = await place_mission_call(settings=settings, services=services, to=to,
                                           goal=goal, requested_by="api")
        return web.json_response(mission_json(mission),
                                 status=502 if mission.status == "failed" else 201)

    async def get_call(request: web.Request) -> web.Response:
        if not settings.api_token:
            raise web.HTTPNotFound()
        if not authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        mission = missions.get(request.match_info["mission_id"])
        if mission is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(mission_json(mission))

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def lifecycle(app: web.Application) -> AsyncIterator[None]:
        services["http"] = aiohttp.ClientSession()
        yield
        await services["http"].close()
        close = getattr(connector, "close", None)
        if close is not None:
            await close()

    app = web.Application()
    app[TOKENS] = tokens
    app[MISSIONS] = missions
    app[CONNECTOR] = connector
    app[DEPS] = deps
    app.cleanup_ctx.append(lifecycle)
    app.router.add_post("/twilio/voice", voice)
    app.router.add_post("/twilio/status", status_callback)
    app.router.add_get("/twilio/stream", stream)
    app.router.add_post("/calls", create_call)
    app.router.add_get("/calls/{mission_id}", get_call)
    app.router.add_get("/healthz", health)
    return app
