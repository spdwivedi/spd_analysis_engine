Step_01: Standardize ChangeSet Domain Object
ID: S05-M01-P01-Step01

Status: DONE

Depends on: S04-M03-P01-Step02

1. Purpose
Define ChangeSet dataclass capturing burst_uid, event_id, file lists, before/after fingerprints, and renames.

2. Scope
spd_analysis_engine/scripts/execution/changeset.py

3. Verification (Done when...)
ChangeSet standardizes burst output format across the entire capture and storage pipeline.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/changeset.py, scripts/git/shadow_repo.py).
- **Test Verification:** Covered by passing test suite (tests/test_changeset_and_shadow_git.py).
