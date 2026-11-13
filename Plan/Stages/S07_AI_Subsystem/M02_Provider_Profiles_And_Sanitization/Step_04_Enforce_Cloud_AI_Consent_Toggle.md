Step_04: Enforce Cloud AI Consent Toggle
ID: S07-M02-P01-Step04

Status: DONE

Depends on: S07-M02-P01-Step03

1. Purpose
Enforce per-project cloud AI consent setting requiring explicit user opt-in before sending sanitized data to cloud providers.

2. Scope
spd_analysis_engine/scripts/ai/config.py, spd_analysis_engine/scripts/server/routes_ai.py

3. Verification (Done when...)
Requests to cloud providers fail with ConsentRequiredError if project consent is disabled.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/ai/profiles.py, scripts/ai/sanitizer.py).
- **Test Verification:** Covered by passing test suite (tests/test_provider_profiles_and_sanitizer.py).
