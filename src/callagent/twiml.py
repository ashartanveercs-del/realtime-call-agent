"""Minimal TwiML builders (no SDK needed for two verbs)."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def connect_stream(stream_url: str, parameters: dict[str, str]) -> str:
    """Bidirectional Media Stream: Twilio sends caller audio to `stream_url` and plays back
    whatever audio we send. When the WebSocket closes, <Connect> ends and so does the call."""
    params = "".join(
        f"<Parameter name={quoteattr(name)} value={quoteattr(value)}/>"
        for name, value in parameters.items()
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Connect><Stream url={quoteattr(stream_url)}>{params}</Stream>"
            "</Connect></Response>")


def say_and_hangup(text: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Say>{escape(text)}</Say><Hangup/></Response>")
