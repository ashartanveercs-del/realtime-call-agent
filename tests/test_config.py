import pytest

from callagent.config import ConfigError, Settings

REQUIRED = {
    "OPENAI_API_KEY": "sk-test",
    "TWILIO_ACCOUNT_SID": "AC00000000000000000000000000000000",
    "TWILIO_AUTH_TOKEN": "secret",
    "TWILIO_PHONE_NUMBER": "+15550000000",
    "PUBLIC_BASE_URL": "https://agent.example.com/",
}


def test_from_env_reads_required_values_and_defaults():
    s = Settings.from_env(REQUIRED)
    assert s.openai_api_key == "sk-test"
    assert s.public_base_url == "https://agent.example.com"
    assert s.stream_url == "wss://agent.example.com/twilio/stream"
    assert s.realtime_model
    assert s.validate_signatures is True
    assert s.owner_numbers == frozenset()


def test_missing_required_values_are_all_reported():
    with pytest.raises(ConfigError) as err:
        Settings.from_env({"OPENAI_API_KEY": "sk-test"})
    message = str(err.value)
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
                 "PUBLIC_BASE_URL"):
        assert name in message


def test_owner_numbers_are_normalised():
    s = Settings.from_env({**REQUIRED, "OWNER_NUMBERS": " +1 555 000 1111, +15550002222 ,"})
    assert s.owner_numbers == frozenset({"+15550001111", "+15550002222"})


def test_numeric_boolean_and_price_settings():
    s = Settings.from_env({**REQUIRED, "MAX_CALL_SECONDS": "300", "VALIDATE_TWILIO_SIGNATURE":
                           "false", "COST_PER_MTOK": "input_audio=32, output_audio=64"})
    assert s.max_call_seconds == 300
    assert s.validate_signatures is False
    assert s.cost_per_mtok == {"input_audio": 32.0, "output_audio": 64.0}


def test_public_url_must_be_https():
    with pytest.raises(ConfigError):
        Settings.from_env({**REQUIRED, "PUBLIC_BASE_URL": "http://agent.example.com"})
