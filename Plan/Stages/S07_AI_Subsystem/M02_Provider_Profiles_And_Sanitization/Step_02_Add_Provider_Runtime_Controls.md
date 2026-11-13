Step_02: Add Provider Runtime Controls
ID: S07-M02-P01-Step02

Status: DONE

Depends on: S07-M02-P01-Step01

1. Purpose
Add provider controls: Gemini rate limiting; Ollama GPU status detection, num_ctx, and keep_alive.

2. Scope
spd_analysis_engine/scripts/ai/provider_gemini.py, spd_analysis_engine/scripts/ai/provider_ollama.py

3. Verification (Done when...)
Ollama queries detect GPU offloading status; Gemini queries respect global token rate limiters.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/ai/profiles.py, scripts/ai/sanitizer.py).
- **Test Verification:** Covered by passing test suite (tests/test_provider_profiles_and_sanitizer.py).
