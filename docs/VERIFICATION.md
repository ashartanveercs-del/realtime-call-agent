# Verification and design evidence

Verified on **2026-10-12**, Windows, Python **3.12.10**. These are local offline results;
the linked GitHub Actions workflow runs the suite on Linux with Python 3.11 and 3.12.

| Command | Observed result |
| --- | --- |
| `python -m pytest -q -ra` | **139 passed**, zero failures or skips |
| `python -m ruff check src tests examples` | All checks passed |
| `python examples/barge_in_demo.py` | Verified 600 ms / 0 ms and 2,000 ms / 500 ms playback cases; queued state cleared |

Commands ran with the repository's `.venv/Scripts/python.exe`; activate a development
environment installed with `python -m pip install -e ".[dev]"` to use the commands above.

## What the tests establish

| Claim | Implementation | Tests |
| --- | --- | --- |
| Truncation uses the playout timeline, including answers queued behind others | [`audio.py`](../src/callagent/audio.py) | [`test_audio.py`](../tests/test_audio.py), [`test_session.py`](../tests/test_session.py) |
| Background jobs are bounded and deduplicated; results wait for caller/model turns | [`jobs.py`](../src/callagent/jobs.py), [`session.py`](../src/callagent/session.py) | [`test_jobs.py`](../tests/test_jobs.py), [`test_session.py`](../tests/test_session.py) |
| Signed inbound requests bind roles to single-use media-stream tokens | [`server.py`](../src/callagent/server.py), [`tokens.py`](../src/callagent/tokens.py) | [`test_server.py`](../tests/test_server.py), [`test_tokens.py`](../tests/test_tokens.py) |
| Malformed outbound-call payloads are rejected before dialing | [`server.py`](../src/callagent/server.py) | Six non-object JSON cases and five non-text goal cases in [`test_server.py`](../tests/test_server.py) |
| Call shutdown waits for playback, with a bounded grace period | [`session.py`](../src/callagent/session.py) | Goodbye, caller-speech, time-limit, and pending-mark cases in [`test_session.py`](../tests/test_session.py) |

## Defect caught during review

Valid JSON arrays, nulls, and primitives previously reached dictionary access in `POST /calls`
and produced HTTP 500. A goal supplied as null, a number, a boolean, a list, or an object
was converted to text and accepted. Eleven new regression cases failed against the original
handler. The corrected boundary returns HTTP 400 without creating an outbound call.

## Scope and limits

- Tests use in-memory model/Twilio doubles and loopback HTTP/WebSocket servers. They require
  no provider credentials and place no phone calls or SMS messages.
- The demo checks the production playout tracker; it does not measure carrier latency,
  speech quality, or live model behavior. No sub-second latency guarantee is claimed.
- Role gates run in code. Spoken confirmation still depends on the model interpreting the
  caller's answer; it is not an independently authenticated approval token.
- Caller-number matching alone is not identity proof. The optional attestation gate and
  remaining integration boundaries are described in the [security notes](../README.md#security-notes).
- Passing offline tests does not certify a deployment or verify current provider compatibility.
