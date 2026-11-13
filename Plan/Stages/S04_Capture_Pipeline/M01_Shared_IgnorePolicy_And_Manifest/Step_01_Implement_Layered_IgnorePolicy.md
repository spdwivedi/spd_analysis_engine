Step_01: Implement Layered IgnorePolicy
ID: S04-M01-P01-Step01

Status: DONE

Depends on: S03-M02-P01-Step04

1. Purpose
Build unified IgnorePolicy combining built-in engine rules, .gitignore, .spdignore, and sensitive credential blocklists.

2. Scope
spd_analysis_engine/scripts/storage/ignore_policy.py

3. Verification (Done when...)
IgnorePolicy evaluates paths accurately without false positives on nested directories named patches or baseline.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/storage/ignore_policy.py, scripts/storage/manifest.py).
- **Test Verification:** Covered by passing test suite (tests/test_ignore_policy_manifest_watcher.py).
