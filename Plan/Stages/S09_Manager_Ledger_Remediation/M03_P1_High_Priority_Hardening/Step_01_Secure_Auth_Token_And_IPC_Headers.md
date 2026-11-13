# Step 01 Secure Auth Token And IPC Headers
ID: S09-M03-Step01
Status: DONE

## 1. Objective & Scope
Remove unauthenticated token disclosure from GET /api/auth/token; enforce Host, Origin, and JSON Content-Type validation on the loopback control channel.

- **Target Files:** `scripts/server/api_contract.py, scripts/server/security.py, scripts/controller/control_channel.py`
- **Test Verification:** `tests/test_server_hardening.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_server_hardening.py`.
- Syntax verification passes: `python -m py_compile scripts/server/api_contract.py`.

## 3. Execution Summary
- **Implementation:** Hardened `GET /api/auth/token` in `request_handler.py` to reject unauthenticated requests without valid cookie/token (401) and block untrusted Origin headers (403). Hardened `control_channel.py` to enforce loopback Host validation (`127.0.0.1`, `localhost`, `::1`), reject cross-origin Simple-POSTs, strictly enforce `application/json` Content-Type (returning HTTP 415 on mismatch), and verify non-empty constant-time worker secret tokens.
- **Test Verification:** All 14 tests in `tests/test_server_hardening.py` passing cleanly (including `test_unauthenticated_token_endpoint_rejected` and `test_worker_control_channel_security`). Line count maintained at 411 lines (<= 500).
