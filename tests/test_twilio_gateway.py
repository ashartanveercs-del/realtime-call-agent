from twilio.request_validator import RequestValidator
from twilio_stubs import StubTwilioClient

from callagent.twilio_gateway import TwilioGateway

URL = "https://agent.example.com/twilio/voice"
PARAMS = {"From": "+15550001111", "CallSid": "CA1"}


def gateway(client=None) -> TwilioGateway:
    return TwilioGateway("AC0", "auth-token", "+15550000000", client=client or StubTwilioClient())


def test_accepts_a_correctly_signed_request():
    signature = RequestValidator("auth-token").compute_signature(URL, PARAMS)
    assert gateway().is_valid_request(URL, PARAMS, signature) is True


def test_rejects_wrong_or_missing_signatures():
    forged = RequestValidator("someone-else").compute_signature(URL, PARAMS)
    assert gateway().is_valid_request(URL, PARAMS, forged) is False
    assert gateway().is_valid_request(URL, PARAMS, "") is False


def test_rejects_a_signed_request_whose_parameters_were_changed():
    signature = RequestValidator("auth-token").compute_signature(URL, PARAMS)
    tampered = {**PARAMS, "From": "+15559999999"}
    assert gateway().is_valid_request(URL, tampered, signature) is False


async def test_start_call_uses_inline_twiml_and_status_callback():
    client = StubTwilioClient()
    sid = await gateway(client).start_call("+15550003333", "<Response/>",
                                           status_callback="https://x/twilio/status?mission=m1")
    assert sid == "CA_OUT_1"
    [created] = client.calls.created
    assert created["to"] == "+15550003333"
    assert created["from_"] == "+15550000000"
    assert created["twiml"] == "<Response/>"
    assert created["status_callback"] == "https://x/twilio/status?mission=m1"


async def test_send_sms_uses_the_agent_number():
    client = StubTwilioClient()
    await gateway(client).send_sms("+15550004444", "On my way")
    assert client.messages.created == [
        {"to": "+15550004444", "from_": "+15550000000", "body": "On my way"}]
