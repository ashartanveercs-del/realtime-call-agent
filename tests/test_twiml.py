import xml.etree.ElementTree as ET

from callagent.twiml import connect_stream, say_and_hangup


def test_connect_stream_points_twilio_at_the_websocket_with_parameters():
    xml = connect_stream("wss://agent.example.com/twilio/stream", {"token": "abc123"})

    root = ET.fromstring(xml)
    stream = root.find("./Connect/Stream")
    assert root.tag == "Response"
    assert stream is not None
    assert stream.get("url") == "wss://agent.example.com/twilio/stream"
    params = {p.get("name"): p.get("value") for p in stream.findall("Parameter")}
    assert params == {"token": "abc123"}


def test_connect_stream_escapes_special_characters():
    xml = connect_stream("wss://h/s?a=1&b=2", {"note": 'say "hi" <now> & go'})

    stream = ET.fromstring(xml).find("./Connect/Stream")
    assert stream.get("url") == "wss://h/s?a=1&b=2"
    assert stream.find("Parameter").get("value") == 'say "hi" <now> & go'


def test_say_and_hangup_speaks_then_ends_the_call():
    root = ET.fromstring(say_and_hangup("Sorry, this line is unavailable."))

    assert [child.tag for child in root] == ["Say", "Hangup"]
    assert root.find("Say").text == "Sorry, this line is unavailable."
