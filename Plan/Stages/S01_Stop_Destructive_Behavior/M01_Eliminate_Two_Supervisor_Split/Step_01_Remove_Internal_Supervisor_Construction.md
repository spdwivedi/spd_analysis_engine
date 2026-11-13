Step_01: Remove Internal Supervisor Construction
ID: S01-M01-P01-Step01

Status: DONE

Depends on: S00-M06-P01-Step01

1. Purpose
Eliminate independent Supervisor instantiation inside EngineWebServer to stop split-brain lifecycle ownership.

2. Scope
spd_analysis_engine/scripts/web_server.py, spd_analysis_engine/scripts/server/server_daemon.py

3. Verification (Done when...)
EngineWebServer requires an authoritative Supervisor passed as a constructor parameter.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/supervisor.py, scripts/controller/controller.py).
- **Test Verification:** Covered by passing test suite (tests/test_authoritative_lifecycle.py).
