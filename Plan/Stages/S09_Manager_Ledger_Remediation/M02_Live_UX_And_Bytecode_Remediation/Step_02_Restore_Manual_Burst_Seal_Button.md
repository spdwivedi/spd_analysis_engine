# Step 02 Restore Manual Burst Seal Button
ID: S09-M02-Step02
Status: DONE

## 1. Objective & Scope
Restore interactive '⚡ Seal Burst Now' button in the dashboard toolbar and connect it to POST /api/project/<slug>/seal-burst.

- **Target Files:** `web/index.html, web/js/views/dashboard.js`
- **Test Verification:** `tests/test_frontend_incremental.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_frontend_incremental.py`.
- Syntax verification passes: `python -m py_compile web/index.html`.

## 3. Execution Summary
- **Implementation:** Added `#btn-force-seal` to header actions toolbar in `web/index.html`, added `DOM.btnForceSeal` getter in `web/js/dom.js`, and updated `web/js/views/dashboard.js` to wire both `DOM.btnSealBurstNow` and `DOM.btnForceSeal` to `POST /api/project/<slug>/seal-burst` with `{ "burst_id": null }` payload, resetting progress bars and displaying success toast.
- **Test Verification:** Added `test_manual_burst_seal_button_wiring` to `tests/test_frontend_incremental.py`. 11 tests passing cleanly.
