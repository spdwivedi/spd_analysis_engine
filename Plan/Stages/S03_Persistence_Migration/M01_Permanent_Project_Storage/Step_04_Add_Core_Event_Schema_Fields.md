Step_04: Add Core Event Schema Fields
ID: S03-M01-P01-Step04

Status: DONE

Depends on: S03-M01-P01-Step03

1. Purpose
Add seq, burst_uid, and effective_files to events table, and size/hash metadata to patches table.

2. Scope
spd_analysis_engine/scripts/storage/schema_migration.py, spd_analysis_engine/scripts/storage/db.py

3. Verification (Done when...)
Schema migration applies new columns cleanly without data loss on existing databases.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/storage/project_storage.py, scripts/storage/migrator.py).
- **Test Verification:** Covered by passing test suite (tests/test_storage_migration.py).
