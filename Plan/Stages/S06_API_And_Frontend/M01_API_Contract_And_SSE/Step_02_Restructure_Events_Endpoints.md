Step_02: Restructure Events Endpoints
ID: S06-M01-P01-Step02

Status: DONE

Depends on: S06-M01-P01-Step01

1. Purpose
Restructure events endpoints: GET /events returns summaries only, GET /events/<id> returns detail, and diffs are loaded lazily.

2. Scope
spd_analysis_engine/scripts/server/routes_events_api.py

3. Verification (Done when...)
Timeline event listing payload size is reduced by >80% by omitting inline diff strings.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/api_contract.py, scripts/server/sse_stream.py).
- **Test Verification:** Covered by passing test suite (tests/test_api_contract_events_sse.py).
