Step_03: Optimize Large File Pathspec Batches
ID: S05-M01-P01-Step03

Status: DONE

Depends on: S05-M01-P01-Step02

1. Purpose
Support large bursts (1,000+ to 2,000+ files) via pathspec-from-file and NUL-separated argument streams.

2. Scope
spd_analysis_engine/scripts/git/shadow_commit.py, spd_analysis_engine/scripts/git/utils.py

3. Verification (Done when...)
A burst modifying 2,000 files commits successfully on Windows without command line length errors.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/changeset.py, scripts/git/shadow_repo.py).
- **Test Verification:** Covered by passing test suite (tests/test_changeset_and_shadow_git.py).
