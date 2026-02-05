# ROONIE Phase 1 Offline Harness

This project implements Phase 1 deterministic, offline test harness for ROONIE-AI. The legacy live script is preserved under `legacy/`.

**Key points**
- Phase 1 runs fully offline: no network calls or model APIs.
- Deterministic and auditable: every event produces a `DecisionRecord` with trace fields.
- Pytest fixtures replay cases and compare to golden outputs.

**Setup (Windows PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
```

**Notes**
- `legacy/roonie_brain_test.py` is the preserved Phase 0 script.
- Phase 1 logic lives in `src/roonie/` and is used by the test harness.

