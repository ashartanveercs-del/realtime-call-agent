"""Thin wrapper over the official Twilio SDK: webhook signatures, outbound calls, SMS."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from twilio.request_validator import RequestValidator
from twilio.rest import Client


class TwilioGateway:
    def __init__(self, account_sid: str, auth_token: str, from_number: str,
                 client: Any | None = None) -> None:
        self._validator = RequestValidator(auth_token)
        self._client = client if client is not None else Client(account_sid, auth_token)
        self._from = from_number

    def is_valid_request(self, url: str, params: Mapping[str, str], signature: str) -> bool:
        """X-Twilio-Signature check: HMAC-SHA1 of the exact public URL plus POST params."""
        if not signature:
            return False
        return bool(self._validator.validate(url, dict(params), signature))

    async def start_call(self, to: str, twiml: str, status_callback: str | None = None) -> str:
        kwargs: dict[str, Any] = {"to": to, "from_": self._from, "twiml": twiml}
        if status_callback:
            kwargs.update(status_callback=status_callback, status_callback_method="POST",
                          status_callback_event=["completed"])
        call = await asyncio.to_thread(self._client.calls.create, **kwargs)
        return str(call.sid)

    async def send_sms(self, to: str, body: str) -> str:
        message = await asyncio.to_thread(self._client.messages.create,
                                          to=to, from_=self._from, body=body)
        return str(message.sid)
