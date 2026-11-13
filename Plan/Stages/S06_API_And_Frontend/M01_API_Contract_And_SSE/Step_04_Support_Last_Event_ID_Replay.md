Step_04: Support Last-Event-ID Replay
ID: S06-M01-P01-Step04

Status: DONE

Depends on: S06-M01-P01-Step03

1. Purpose
Support Last-Event-ID header on SSE reconnections to replay missed events seamlessly.

2. Scope
spd_analysis_engine/scripts/server/sse_stream.py

3. Verification (Done when...)
Reconnecting client receives missed burst_sealed events since the specified Last-Event-ID.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/api_contract.py, scripts/server/sse_stream.py).
- **Test Verification:** Covered by passing test suite (tests/test_api_contract_events_sse.py).
