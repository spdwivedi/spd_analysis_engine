Step_02: Fetch Summary On Burst Sealed
ID: S06-M02-P01-Step02

Status: DONE

Depends on: S06-M02-P01-Step01

1. Purpose
Update frontend to fetch only the newly sealed event summary upon receiving a burst_sealed SSE notification.

2. Scope
spd_analysis_engine/web/js/components/events.js

3. Verification (Done when...)
Receiving burst_sealed appends the new event to the timeline via a single lightweight API call.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/web/js/api/events.js, web/js/components/windowed_list.js).
- **Test Verification:** Covered by passing test suite (tests/test_frontend_incremental.py).
