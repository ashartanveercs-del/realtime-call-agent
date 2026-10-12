from aiohttp import WSMsgType, web

from callagent.realtime import OpenAIRealtimeConnector


async def test_connector_authenticates_selects_the_model_and_relays_json(aiohttp_server):
    seen = {}

    async def realtime(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["model"] = request.query.get("model")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "session.created"})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await ws.send_str(msg.data)  # echo
        return ws

    app = web.Application()
    app.router.add_get("/v1/realtime", realtime)
    server = await aiohttp_server(app)
    url = str(server.make_url("/v1/realtime")).replace("http://", "ws://")

    connector = OpenAIRealtimeConnector("sk-test", "gpt-realtime-test", url)
    sock = await connector.connect()
    messages = aiter(sock)

    assert await anext(messages) == {"type": "session.created"}
    await sock.send({"type": "input_audio_buffer.append", "audio": "AAAA"})
    assert await anext(messages) == {"type": "input_audio_buffer.append", "audio": "AAAA"}
    assert seen == {"auth": "Bearer sk-test", "model": "gpt-realtime-test"}

    await sock.close()
    assert sock.closed
    await connector.close()
