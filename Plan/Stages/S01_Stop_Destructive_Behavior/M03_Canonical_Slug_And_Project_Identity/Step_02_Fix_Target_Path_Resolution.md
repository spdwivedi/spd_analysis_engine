Step_02: Fix Target Path Resolution
ID: S01-M03-P01-Step02

Status: DONE

Depends on: S01-M03-P01-Step01

1. Purpose
Fix _resolve_target_path() across server routes so slug is the single authoritative key for runtime lookups.

2. Scope
spd_analysis_engine/scripts/server/request_handler.py, spd_analysis_engine/scripts/server/routes_projects.py

3. Verification (Done when...)
All API endpoints correctly locate project directories using slugs without falling back to ambiguous display names.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/project_config.py).
- **Test Verification:** Covered by passing test suite (tests/test_identity_and_slug_resolution.py).
