"""Run the agent with tools of your own.

    python examples/custom_tools.py --env-file .env
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from aiohttp import web

from callagent.__main__ import read_env_file
from callagent.builtin_tools import build_registry
from callagent.config import Settings
from callagent.server import create_app
from callagent.tools import ToolContext


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    env = {**(read_env_file(args.env_file) if args.env_file.exists() else {}), **os.environ}
    settings = Settings.from_env(env)

    registry = build_registry(settings)

    # A fast tool: answers inline in well under a second.
    @registry.tool(
        description="Look up the delivery status of an order by its order number.",
        roles=("owner", "guest"),
        parameters={"type": "object", "required": ["order_id"],
                    "properties": {"order_id": {"type": "string"}}})
    async def order_status(ctx: ToolContext, order_id: str) -> dict:
        # Replace with a call to your own system.
        return {"ok": True, "order_id": order_id, "status": "out for delivery",
                "eta": "today between 4 and 6 PM"}

    # A background tool: the agent keeps talking while this runs, then shares the result.
    @registry.tool(
        description="Research a topic and come back with a short summary.",
        roles=("owner",), background=True, timeout=120,
        label=lambda args: f"research on {args.get('topic', 'a topic')}",
        parameters={"type": "object", "required": ["topic"],
                    "properties": {"topic": {"type": "string"}}})
    async def research(ctx: ToolContext, topic: str) -> str:
        await asyncio.sleep(8)  # stand-in for slow work (web search, an agent, a database)
        return f"Here are the three most useful things I found about {topic}: ..."

    web.run_app(create_app(settings, registry=registry), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
