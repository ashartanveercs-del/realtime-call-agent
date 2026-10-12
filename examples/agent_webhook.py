"""A minimal "brain" for the delegate_task tool.

Contract: POST {"task", "caller", "call_sid"} with `Authorization: Bearer <API_TOKEN>`,
respond {"answer": "..."} within ~170 s. Swap `answer()` for your real agent
(LangGraph, the OpenAI Agents SDK, the Claude Agent SDK, ...).

    API_TOKEN=change-me python examples/agent_webhook.py
    # then set AGENT_WEBHOOK_URL=http://localhost:8090/agent for the call agent
"""

from __future__ import annotations

import hmac
import os

from aiohttp import web

TOKEN = os.environ.get("API_TOKEN", "")


async def answer(task: str) -> str:
    return (f"I looked into '{task}'. This demo agent has no real tools yet - plug yours in "
            "here and I'll read the result back to the caller.")


async def handle(request: web.Request) -> web.Response:
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if TOKEN and not hmac.compare_digest(supplied.encode(), TOKEN.encode()):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await request.json()
    return web.json_response({"answer": await answer(str(body.get("task", "")))})


app = web.Application()
app.router.add_post("/agent", handle)

if __name__ == "__main__":
    web.run_app(app, port=int(os.environ.get("PORT", "8090")))
