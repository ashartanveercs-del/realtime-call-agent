"""Built-in tools. Add your own with `registry.tool(...)` - see examples/custom_tools.py."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

from .config import Settings, is_e164, normalise_number
from .outbound import place_mission_call
from .tools import ToolContext, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

ALL_ROLES = ("owner", "guest", "mission")


def load_contacts(path: Path | str | None) -> dict[str, str]:
    """contacts.json: {"Alex Chen": "+15550004444", ...}"""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name.strip().lower(): normalise_number(number) for name, number in data.items()}


def resolve_number(who: str, contacts: dict[str, str]) -> str | None:
    candidate = normalise_number(who)
    if is_e164(candidate):
        return candidate
    return contacts.get(who.strip().lower())


def _spoken_time(now: datetime) -> str:
    hour = now.strftime("%I").lstrip("0") or "12"
    return f"It's {hour}:{now:%M} {now:%p} on {now:%A}, {now:%B} {now.day}."


def build_registry(settings: Settings) -> ToolRegistry:
    reg = ToolRegistry()
    owner = settings.owner_name

    @reg.tool(description="End the phone call. Say a short goodbye first, then call this.",
              roles=ALL_ROLES)
    async def end_call(ctx: ToolContext) -> ToolResult:
        ctx.hangup()
        return ToolResult({"ok": True}, respond=False)

    @reg.tool(description="Get the current local date and time.", roles=ALL_ROLES)
    async def current_time(ctx: ToolContext) -> str:
        zone = ZoneInfo(ctx.settings.timezone)
        now_fn = ctx.services.get("now") or (lambda tz: datetime.now(tz))
        return _spoken_time(now_fn(zone))

    @reg.tool(description=(f"Take a message for {owner}. Ask for the caller's name and what "
                           "they need first. The message is only taken if this returns ok."),
              roles=("guest",),
              parameters={"type": "object", "required": ["message"], "properties": {
                  "message": {"type": "string", "description": "The message, in full."},
                  "caller_name": {"type": "string", "description": "Caller's name, if given."}}})
    async def take_message(ctx: ToolContext, message: str, caller_name: str = "") -> dict:
        ctx.store.add_message(caller=ctx.caller, text=message, call_sid=ctx.call_sid)
        relayed = False
        gateway = ctx.services.get("gateway")
        if gateway is not None and ctx.settings.owner_numbers:
            who = f"{caller_name} ({ctx.caller})" if caller_name else ctx.caller
            for number in sorted(ctx.settings.owner_numbers):
                try:
                    await gateway.send_sms(number, f"Message from {who}: {message}"[:1500])
                    relayed = True
                except Exception:
                    log.exception("could not relay message to owner")
        return {"ok": True, "saved": True, "relayed_by_text": relayed}

    @reg.tool(description=(f"Send a text message on {owner}'s behalf. Before calling this, read "
                           "back the recipient and the exact wording and get a yes."),
              roles=("owner",), timeout=15,
              parameters={"type": "object", "required": ["to", "body"], "properties": {
                  "to": {"type": "string", "description": "Contact name or +E.164 number."},
                  "body": {"type": "string", "description": "Exact message text."}}})
    async def send_sms(ctx: ToolContext, to: str, body: str) -> dict:
        number = resolve_number(to, ctx.services.get("contacts", {}))
        if number is None:
            return {"ok": False, "error": f"I don't have a number for {to}."}
        await ctx.services["gateway"].send_sms(number, body)
        return {"ok": True, "to": number}

    @reg.tool(description=("Place a real phone call to someone on the owner's behalf to get "
                           "something done. Runs in the background while you keep talking; "
                           "what the other person said comes back to you when that call ends."),
              roles=("owner",), background=True, timeout=900,
              label=lambda args: f"calling {args.get('who', 'someone')}",
              parameters={"type": "object", "required": ["who", "goal"], "properties": {
                  "who": {"type": "string", "description": "Contact name or +E.164 number."},
                  "goal": {"type": "string",
                           "description": "What to accomplish or ask, with all details."}}})
    async def call_person(ctx: ToolContext, who: str, goal: str) -> str:
        number = resolve_number(who, ctx.services.get("contacts", {}))
        if number is None:
            return f"I couldn't find a phone number for {who}."
        mission = await place_mission_call(settings=ctx.settings, services=ctx.services,
                                           to=number, goal=goal, requested_by=ctx.caller)
        outcome = await mission.outcome()
        if mission.status == "failed":
            return f"The call to {who} didn't go through: {outcome}"
        return f"The call to {who} has ended. Here is what was said:\n{outcome}"

    if settings.agent_webhook_url:
        @reg.tool(description=("Hand any other task or question to the owner's main AI agent "
                               "(email, calendar, research...). Runs in the background; the "
                               "answer comes back to you when it's ready."),
                  roles=("owner",), background=True, timeout=180,
                  label=lambda args: str(args.get("task") or "a task")[:60],
                  parameters={"type": "object", "required": ["task"], "properties": {
                      "task": {"type": "string",
                               "description": "The task, fully specified with every detail."}}})
        async def delegate_task(ctx: ToolContext, task: str) -> str:
            headers = ({"Authorization": f"Bearer {ctx.settings.api_token}"}
                       if ctx.settings.api_token else {})
            payload = {"task": task, "caller": ctx.caller, "call_sid": ctx.call_sid}
            async with ctx.services["http"].post(
                    ctx.settings.agent_webhook_url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=170)) as resp:
                resp.raise_for_status()
                data = await resp.json()
            return str(data.get("answer") or "The agent finished without an answer.")

    return reg
