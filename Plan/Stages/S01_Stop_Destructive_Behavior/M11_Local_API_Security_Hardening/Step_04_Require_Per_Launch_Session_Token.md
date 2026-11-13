Step_04: Require Per-Launch Session Token
ID: S01-M11-P01-Step04

Status: DONE

Depends on: S01-M11-P01-Step03

1. Purpose
Generate an ephemeral loopback API authentication token per engine launch and require it for state mutations.

2. Scope
spd_analysis_engine/scripts/server/security.py, spd_analysis_engine/scripts/web_server.py

3. Verification (Done when...)
State-changing API calls without the valid authorization token return HTTP 401 Unauthorized.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/server/security.py, scripts/server/request_handler.py).
- **Test Verification:** Covered by passing test suite (tests/test_server_hardening.py).
