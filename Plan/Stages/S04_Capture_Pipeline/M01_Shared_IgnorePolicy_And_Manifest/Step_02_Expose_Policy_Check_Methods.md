Step_02: Expose Policy Check Methods
ID: S04-M01-P01-Step02

Status: DONE

Depends on: S04-M01-P01-Step01

1. Purpose
Expose granular check methods: should_watch, should_index, should_diff, should_stage, should_send_to_ai.

2. Scope
spd_analysis_engine/scripts/storage/ignore_policy.py

3. Verification (Done when...)
Each check method returns correct boolean results for sensitive keys, build outputs, and code files.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/storage/ignore_policy.py, scripts/storage/manifest.py).
- **Test Verification:** Covered by passing test suite (tests/test_ignore_policy_manifest_watcher.py).
