"""Placing outbound "mission" calls (shared by the REST API and the call_person tool)."""

from __future__ import annotations

import logging
from typing import Any

from .config import Settings
from .missions import Mission, MissionRegistry
from .tokens import TokenStore
from .twiml import connect_stream

log = logging.getLogger(__name__)


async def place_mission_call(*, settings: Settings, services: dict[str, Any], to: str,
                             goal: str, requested_by: str) -> Mission:
    missions: MissionRegistry = services["missions"]
    tokens: TokenStore = services["tokens"]
    mission = missions.create(to=to, goal=goal, requested_by=requested_by)
    token = tokens.mint({"role": "mission", "caller": to, "mission_id": mission.id})
    twiml = connect_stream(settings.stream_url, {"token": token})
    callback = f"{settings.public_base_url}/twilio/status?mission={mission.id}"
    try:
        mission.call_sid = await services["gateway"].start_call(to, twiml, status_callback=callback)
    except Exception:
        log.exception("could not place mission call %s", mission.id)
        missions.fail(mission.id, "The call could not be placed.")
    return mission
