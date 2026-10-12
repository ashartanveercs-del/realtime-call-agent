"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    pass


REQUIRED = ("OPENAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
            "TWILIO_PHONE_NUMBER", "PUBLIC_BASE_URL")


E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalise_number(raw: str) -> str:
    """'+1 (555) 000-1111' -> '+15550001111'. Keeps a leading '+', drops everything else."""
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if raw.startswith("+") else digits


def is_e164(number: str) -> bool:
    return bool(E164.match(number))


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _prices(value: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for part in filter(None, (p.strip() for p in value.split(","))):
        key, _, amount = part.partition("=")
        prices[key.strip()] = float(amount)
    return prices


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    public_base_url: str
    realtime_model: str = "gpt-realtime-2.1-mini"
    realtime_voice: str = "marin"
    realtime_url: str = "wss://api.openai.com/v1/realtime"
    transcription_model: str | None = "whisper-1"
    agent_name: str = "Robin"
    owner_name: str = "the owner"
    owner_numbers: frozenset[str] = frozenset()
    owner_require_attestation: bool = False
    api_token: str | None = None
    agent_webhook_url: str | None = None
    contacts_file: Path | None = None
    data_dir: Path = Path("data")
    timezone: str = "UTC"
    max_call_seconds: int = 900
    silence_nudge_seconds: float = 30.0
    silence_end_seconds: float = 75.0
    max_background_jobs: int = 3
    validate_signatures: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    cost_per_mtok: dict[str, float] = field(default_factory=dict)

    @property
    def stream_url(self) -> str:
        return "wss://" + self.public_base_url.removeprefix("https://") + "/twilio/stream"

    def is_owner(self, number: str) -> bool:
        return bool(number) and normalise_number(number) in self.owner_numbers

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        missing = [name for name in REQUIRED if not env.get(name)]
        if missing:
            raise ConfigError("Missing required settings: " + ", ".join(missing))
        base = env["PUBLIC_BASE_URL"].strip().rstrip("/")
        if not base.startswith("https://"):
            raise ConfigError("PUBLIC_BASE_URL must start with https:// (Twilio needs TLS)")

        def opt(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        kwargs: dict[str, object] = {
            "openai_api_key": env["OPENAI_API_KEY"],
            "twilio_account_sid": env["TWILIO_ACCOUNT_SID"],
            "twilio_auth_token": env["TWILIO_AUTH_TOKEN"],
            "twilio_phone_number": normalise_number(env["TWILIO_PHONE_NUMBER"]),
            "public_base_url": base,
            "owner_numbers": frozenset(
                normalise_number(n) for n in env.get("OWNER_NUMBERS", "").split(",") if n.strip()
            ),
            "api_token": opt("API_TOKEN"),
            "agent_webhook_url": opt("AGENT_WEBHOOK_URL"),
        }
        for name, attr in (("REALTIME_MODEL", "realtime_model"),
                           ("REALTIME_VOICE", "realtime_voice"),
                           ("REALTIME_URL", "realtime_url"), ("AGENT_NAME", "agent_name"),
                           ("OWNER_NAME", "owner_name"), ("TIMEZONE", "timezone"),
                           ("HOST", "host")):
            if opt(name):
                kwargs[attr] = opt(name)
        if "TRANSCRIPTION_MODEL" in env:
            kwargs["transcription_model"] = opt("TRANSCRIPTION_MODEL")
        if opt("CONTACTS_FILE"):
            kwargs["contacts_file"] = Path(env["CONTACTS_FILE"])
        if opt("DATA_DIR"):
            kwargs["data_dir"] = Path(env["DATA_DIR"])
        for name, attr in (("MAX_CALL_SECONDS", "max_call_seconds"),
                           ("MAX_BACKGROUND_JOBS", "max_background_jobs"), ("PORT", "port")):
            if opt(name):
                kwargs[attr] = int(env[name])
        for name, attr in (("SILENCE_NUDGE_SECONDS", "silence_nudge_seconds"),
                           ("SILENCE_END_SECONDS", "silence_end_seconds")):
            if opt(name):
                kwargs[attr] = float(env[name])
        if opt("VALIDATE_TWILIO_SIGNATURE"):
            kwargs["validate_signatures"] = _bool(env["VALIDATE_TWILIO_SIGNATURE"])
        if opt("OWNER_REQUIRE_ATTESTATION"):
            kwargs["owner_require_attestation"] = _bool(env["OWNER_REQUIRE_ATTESTATION"])
        if opt("COST_PER_MTOK"):
            kwargs["cost_per_mtok"] = _prices(env["COST_PER_MTOK"])
        return cls(**kwargs)  # type: ignore[arg-type]
