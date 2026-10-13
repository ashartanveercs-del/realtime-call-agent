# realtime-call-agent

**Interruptible voice agents with playback-aware memory and concurrent background tools.**
Bridge Twilio Media Streams to OpenAI Realtime, keep talking while slow work runs, and launch
outbound call missions. The session coordinates what was generated, what was actually heard,
and when a background result can safely enter the conversation.

[![CI](https://github.com/ashartanveercs-del/realtime-call-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ashartanveercs-del/realtime-call-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/offline_tests-139-brightgreen)

[Try the offline demo](#try-the-interruption-engine-offline) · [Architecture](#architecture) ·
[Integrate your agent](#plug-in-your-own-agent) · [Verification](docs/VERIFICATION.md)

## Engineering highlights

| Hard problem | Implementation | Reproducible evidence |
| --- | --- | --- |
| Interrupting buffered speech | A playout timeline truncates every interrupted model item to the audio the caller heard, including replies still queued behind it. | [Playback edge cases](tests/test_audio.py), [whole-call tests](tests/test_session.py) |
| Slow tools during a live conversation | Bounded, deduplicated background jobs feed a response queue that waits for both caller and model turns. | [Job lifecycle tests](tests/test_jobs.py), [result delivery tests](tests/test_session.py) |
| Caller identity and action boundaries | Signed webhooks mint single-use stream tokens; role checks and exact-argument proposals sit in the tool dispatch path. | [HTTP and input validation](tests/test_server.py), [token replay tests](tests/test_tokens.py) |

### Try the interruption engine offline

```bash
python -m pip install -e ".[dev]"
python examples/barge_in_demo.py
```

```text
Caller interrupts at | A heard | B heard | Truncate these items
                1600 |     600 |       0 | [('answer-A', 600), ('answer-B', 0)]
                3500 |    2000 |     500 | [('answer-B', 500)]
                5000 |    2000 |    2000 | []
```

This executes the same tracker used by live call sessions, checks the expected cut points,
and exits with an error if they change. No phone, credentials, or network connection needed.
The conversation below illustrates the live integration flow.

```text
Owner:  "Hey, can you call the bakery and check if my cake is ready, and text Alex I'm running late?"
Agent:  "Sure. I'll text Alex 'Running about 15 minutes late' and call the bakery about your cake. Go ahead?"
Owner:  "Yep."
Agent:  "Sent. I'm calling the bakery now; I'll tell you what they say."
        ... (a second call happens in the background while you keep chatting) ...
Agent:  "The bakery says your cake is ready and they're open until 7."
```

## What's inside

| | |
|---|---|
| **Speech-to-speech, no transcoding** | Twilio Media Streams deliver G.711 μ-law at 8 kHz; the Realtime API accepts and emits the same format (`audio/pcmu`), so audio frames are relayed untouched in both directions. |
| **Barge-in that matches what was heard** | When the caller interrupts, playback is flushed and the model's memory is truncated to the exact millisecond the caller actually heard, not what was sent. This holds even when another reply is still queued behind the one playing. |
| **Two-speed tools** | Fast tools answer inline. Slow ones run as background jobs, deduplicated and capped, so the conversation never stalls. Their results are spoken only when the caller can hear them. |
| **Outbound "missions"** | `call_person` (or `POST /calls`) phones someone to pursue a goal. The callee leg is its own AI session; its transcript comes back as the result. |
| **Caller roles** | Owner, guest and mission callers each get a different persona and a different set of tools. Guests can only leave a message, which is relayed to you by SMS. |
| **Secure by default** | Twilio webhook signatures are verified, streams are bound by one-time tokens, and owner mode can require STIR/SHAKEN attestation. Tool access is checked server-side and errors are never read aloud. Texting or calling someone needs the owner's spoken go-ahead, which is enforced in code. Background results, including what other people said on a mission call, reach the model as tool output and never as system messages. |
| **Call hygiene** | Silence gets a nudge and then a goodbye. There's a hard time limit. Hang-up waits, up to a short grace period, for the caller to finish speaking and the goodbye to finish playing. Long tasks get progress updates. |
| **Observability** | Every call writes a JSONL transcript and a summary with token usage and an optional cost estimate. |
| **Tested without a phone** | 139 tests drive whole calls through in-memory fakes of Twilio and the model, covering barge-in, turn-taking, background jobs, confirmations, hang-ups, missions and the HTTP layer. |

## Architecture

```mermaid
sequenceDiagram
    participant C as Caller
    participant T as Twilio
    participant A as callagent
    participant M as OpenAI Realtime
    C->>T: dials your number
    T->>A: POST /twilio/voice (signed)
    A-->>T: TwiML <Connect><Stream> + one-time token
    T->>A: WebSocket /twilio/stream (start + token)
    A->>M: WebSocket: session.update (persona, tools, audio/pcmu)
    loop every 20 ms
        T->>A: media (μ-law, caller audio)
        A->>M: input_audio_buffer.append
    end
    M-->>A: response.output_audio.delta
    A-->>T: media + mark
    T-->>C: plays the reply
    M-->>A: function_call (e.g. send_sms)
    A-->>M: function_call_output, then response.create
```

Each call runs three concurrent loops ([`session.py`](src/callagent/session.py)):

```mermaid
flowchart LR
    TW[Twilio stream] -- caller audio, timestamps, marks --> P1(twilio pump)
    P1 -- audio --> RT[Realtime session]
    RT -- audio, transcripts, tool calls --> P2(model pump)
    P2 -- media + mark / clear --> TW
    P2 -- fast tool --> T1[inline, timeout]
    P2 -- slow tool --> J[JobManager: dedupe, cap]
    J -- result --> Q[delivery queue]
    W(watchdog) -- hang-up, silence, time limit, progress --> Q
    Q -- only when the line is free --> RT
```

### The parts that are harder than they look

**1. Truncating to what the caller heard.** The model streams audio much faster than real
time, so "bytes sent" overstates what was played. Twilio's inbound frames carry a
stream-clock timestamp, and Twilio plays the audio it receives back to back. So each chunk
gets a slot on a playout timeline. The slot starts when the audio queued ahead of it runs
out, or on arrival if the line is idle, and lasts as long as the chunk's audio. What the
caller heard of a reply is the part of its slots that the stream clock has already passed
([`audio.py`](src/callagent/audio.py)). This stays correct when a second reply is queued
behind the first. On barge-in the session sends Twilio a `clear` and the model a
`response.cancel`. It then sends a `conversation.item.truncate` for every reply that wasn't
heard in full; a reply that never started playing is truncated at 0. Audio of the cancelled
reply that is still in flight is dropped until the next response starts. If the caller heard
a meaningful chunk, the session adds a short system note so the model knows it was cut off
and doesn't repeat itself.

**2. One response at a time.** The Realtime API allows a single in-flight response. Every
response the agent wants (tool follow-ups, background results, nudges, wrap-ups) goes through
one queue. That queue waits while a response is generating or the caller is talking. When the
server starts a turn on its own, queued plain "please respond" requests are dropped, because
that turn already sees the new context. A request the server refuses because a turn is
already running is retried after that turn. A request the model never acknowledges stops
blocking the queue after 5 seconds. Background results are added as tool output and spoken
only when the line is free.

**3. Knowing when audio has actually played.** After each audio chunk the agent sends a Twilio
`mark`; Twilio echoes it once playback reaches it. Pending marks mean audio is still queued on
the line. End-of-call waits for them, and for the caller to finish a sentence, so the goodbye
isn't cut off; a short grace period caps the wait. Barge-in uses the marks to decide whether
there is anything to interrupt.

**4. Identity can't come from the stream.** The incoming-call webhook is signature-verified,
so it decides the caller's role and mints a single-use token. The token goes inside the TwiML
`<Stream>` parameters. A WebSocket without an unused, unexpired token is hung up before any
model session opens.

**5. Other people's words are data, not orders.** A mission call's transcript is a stranger
talking, and a delegated task's answer comes from another system. Both reach the model as
tool output attached to a function call item, never as a system message. A prompt alone
can't stop a model that has been talked into something, so tools that act on the owner's
behalf are also gated in code ([`session.py`](src/callagent/session.py)). The first
`send_sms` or `call_person` call only proposes the action. It runs when the model repeats the
call with identical arguments right after the owner's next utterance.

## Quick start

Requirements: Python 3.11+, a Twilio phone number, an OpenAI API key with Realtime access, and
a public HTTPS URL that reaches your machine (e.g. `ngrok http 8080` or `cloudflared tunnel`).

```bash
git clone https://github.com/ashartanveercs-del/realtime-call-agent
cd realtime-call-agent
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                  # fill in keys, numbers and PUBLIC_BASE_URL
python -m callagent --env-file .env
```

In the Twilio console, set your number's **"A call comes in"** webhook to
`POST https://<your-public-host>/twilio/voice`. Call the number.

Or with Docker: `docker compose up --build`.

### Make an outbound call

```bash
curl -X POST https://<your-public-host>/calls \
  -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"to": "+15551234567", "goal": "Ask whether my order #4411 has shipped."}'
# -> {"mission_id": "m_...", "status": "dialing", ...}

curl -H "Authorization: Bearer $API_TOKEN" https://<your-public-host>/calls/m_...
# -> {"status": "completed", "outcome": "Them: It shipped this morning...\nRobin: ..."}
```

Unanswered, busy or failed calls are reported through Twilio's status callback, so a mission
never waits forever.

## Built-in tools

| Tool | Roles | Speed | What it does |
|---|---|---|---|
| `end_call` | all | fast | Hangs up after the goodbye has finished playing |
| `current_time` | all | fast | Local date and time in `TIMEZONE`, phrased for speech |
| `take_message` | guest | fast | Saves the message and texts it to the owner |
| `send_sms` | owner | fast | Texts a contact or number once the owner confirms the read-back (enforced server-side) |
| `call_person` | owner | background | Once the owner confirms, places a mission call and reports what was said |
| `delegate_task` | owner | background | Hands any task to your own agent over a webhook (enabled when `AGENT_WEBHOOK_URL` is set) |

### Add your own

```python
from callagent.builtin_tools import build_registry
from callagent.server import create_app

registry = build_registry(settings)

@registry.tool(description="Check an order's delivery status.", roles=("owner", "guest"),
               parameters={"type": "object", "required": ["order_id"],
                           "properties": {"order_id": {"type": "string"}}})
async def order_status(ctx, order_id):
    return {"ok": True, "status": "out for delivery", "eta": "today 4-6 PM"}

app = create_app(settings, registry=registry)
```

Pass `background=True` for anything that takes more than a couple of seconds, and give it a
`label` so progress updates sound natural. Pass `confirm=True` for tools that act on someone's
behalf (sending, booking, paying). The first call then only proposes the action, and it runs
once the model repeats it after the caller's go-ahead. Return `ToolResult(..., respond=False)`
when the model shouldn't speak after the tool. A runnable example is in
[`examples/custom_tools.py`](examples/custom_tools.py).

### Plug in your own agent

With `AGENT_WEBHOOK_URL` set, the owner can ask for anything ("what's on my calendar
tomorrow?") and the request is POSTed to your agent as `{"task", "caller", "call_sid"}` with
`Authorization: Bearer $API_TOKEN`. Respond with `{"answer": "..."}`; it's spoken when ready. A
minimal server is in [`examples/agent_webhook.py`](examples/agent_webhook.py). Put LangGraph,
the OpenAI Agents SDK, the Claude Agent SDK or anything else behind it.

## Configuration

| Variable | Default | |
|---|---|---|
| `OPENAI_API_KEY` | required | |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` | required | |
| `PUBLIC_BASE_URL` | required | `https://...` that Twilio can reach |
| `REALTIME_MODEL` | `gpt-realtime-2.1-mini` | any Realtime speech-to-speech model |
| `REALTIME_VOICE` | `marin` | |
| `TRANSCRIPTION_MODEL` | `whisper-1` | empty to disable transcripts |
| `AGENT_NAME` / `OWNER_NAME` | `Robin` / `the owner` | used in personas |
| `OWNER_NUMBERS` | — | comma-separated E.164 numbers that get owner mode |
| `OWNER_REQUIRE_ATTESTATION` | `false` | owner mode only with STIR/SHAKEN `TN-Validation-Passed-A` (US/France) |
| `API_TOKEN` | — | enables `/calls`; also sent to your agent webhook |
| `AGENT_WEBHOOK_URL` | — | enables `delegate_task` |
| `CONTACTS_FILE` | — | JSON map of names to numbers ([example](examples/contacts.example.json)) |
| `TIMEZONE` | `UTC` | |
| `MAX_CALL_SECONDS` | `900` | |
| `SILENCE_NUDGE_SECONDS` / `SILENCE_END_SECONDS` | `30` / `75` | |
| `MAX_BACKGROUND_JOBS` | `3` | per call |
| `COST_PER_MTOK` | — | e.g. `input_audio=..,output_audio=..,input_text=..,output_text=..,cached=..` (USD per 1M tokens, from current pricing) |
| `DATA_DIR` | `data` | transcripts, summaries, messages |
| `VALIDATE_TWILIO_SIGNATURE` | `true` | only disable for local experiments |

## Testing

```bash
pytest            # 139 tests; no external services or phone needed
ruff check src tests examples
python examples/barge_in_demo.py
```

[`tests/fakes.py`](tests/fakes.py) provides in-memory sockets for the Twilio stream and the
model, plus a manual clock, so tests can script exact event orders and timers. One test, for
example, checks that an interruption 600 ms into a 2-second reply truncates the model's
memory at exactly 600 ms. Another checks that a reply queued behind an earlier one is
truncated from the moment it actually started playing.
HTTP routes are tested with real Twilio signatures, and the Realtime connector is tested
against a local WebSocket server.

## Security notes

- **Caller ID can be spoofed.** `OWNER_NUMBERS` alone is convenient but not proof of identity.
  Use `OWNER_REQUIRE_ATTESTATION=true` where STIR/SHAKEN is available, and keep owner-only
  tools to actions you're comfortable gating that way.
- Guests only ever see guest tools. The model can't call a tool outside the caller's role even
  if it hallucinates the name.
- Texting and calling people need the owner's go-ahead, and so does any tool registered with
  `confirm=True`. The first call only returns a proposal. The session runs the action only when
  the model repeats it with identical arguments right after the owner's next utterance. That
  proves the owner was asked and answered, but the model still decides whether the answer was
  a yes, so keep truly high-stakes actions behind checks of your own.
- Background results reach the model as tool output marked as untrusted, never as system
  messages. That includes what the other person said on a mission call. `delegate_task` hands
  requests to your own agent, which should confirm its own side effects.
- Tool failures return a generic message and are logged with details. Internal errors are never
  spoken.

## Responsible use

Outbound calls open by saying they come from an AI assistant calling on your behalf. Follow the
consent, telemarketing and call-recording laws where you and the people you call live (for
example, the TCPA in the US). Don't use this for unsolicited or bulk calling.

## Roadmap

- SIP mode for speech-to-speech models that terminate SIP directly, removing the media relay
- Pluggable memory (per-caller context via the `context_provider` hook)
- WebRTC / browser client alongside the phone line
- Multi-language voice and auto language detection

## License

MIT
