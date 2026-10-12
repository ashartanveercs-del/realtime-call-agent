"""One-time tokens that bind a Twilio media stream to a verified caller identity.

The HTTP voice webhook is signature-checked, so whatever it decides about the caller
(owner / guest / outbound mission) is trustworthy. It mints a short-lived token and
embeds it in the TwiML <Stream> parameters. The WebSocket handler accepts a stream only
if it presents an unused, unexpired token - identity never comes from the stream itself.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any


class TokenStore:
    def __init__(self, ttl_seconds: float = 120.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._tokens: dict[str, tuple[float, dict[str, Any]]] = {}

    def __len__(self) -> int:
        return len(self._tokens)

    def mint(self, payload: dict[str, Any]) -> str:
        self._purge()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (self._clock() + self._ttl, dict(payload))
        return token

    def claim(self, token: str) -> dict[str, Any] | None:
        entry = self._tokens.pop(token, None) if token else None
        if entry is None:
            return None
        expires_at, payload = entry
        return payload if self._clock() <= expires_at else None

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, (exp, _) in self._tokens.items() if exp < now]:
            del self._tokens[token]
