# Roonie AI Control Room

Roonie is a local-first Twitch chat assistant with a secured dashboard, setup-gated auth onboarding, and multi-provider LLM routing for live operations.

## Current Runtime (2026-02-23)
- Twitch auth flow: Device Code (`ROONIE_TWITCH_AUTH_FLOW=device_code`)
- Setup gate: enforced by default (`ROONIE_ENFORCE_SETUP_GATE=1`)
- Providers: OpenAI, Grok, Anthropic (`active_provider` or `random_approved`)
- Moderation: OpenAI moderation is applied to non-OpenAI outputs
- Credentials: Twitch tokens and LLM API keys support encrypted at-rest storage (DPAPI-backed on Windows)

## Core Runtime Flow
```text
Twitch Chat/EventSub
  -> LiveChatBridge
  -> ProviderDirector (active_provider | random_approved)
  -> OutputGate (safety + suppression rules)
  -> TwitchOutputAdapter
```

Dashboard API and UI are served from the same runtime process (`run_control_room.py` + `src/roonie/dashboard_api/app.py`).

## Requirements
- Windows 10/11 (recommended for the primary runtime path and DPAPI behavior)
- Python 3.11+
- Node.js 18+ (for frontend build)

## Quick Start (Windows PowerShell)
1. Create and activate the virtual environment.
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Prepare local config (ignored by git).
```powershell
Copy-Item .env.example config\secrets.env
```

3. Build the dashboard frontend.
```powershell
cd frontend
npm install
npm run build
cd ..
```

4. Launch the live runtime.
```powershell
.\START_ROONIE_LIVE.cmd
```

5. Open the dashboard at `http://127.0.0.1:8787`.

## First-Run Auth Onboarding (Device Code)
1. Log in to the dashboard (set `ROONIE_DASHBOARD_ART_PASSWORD` and `ROONIE_DASHBOARD_JEN_PASSWORD` in env for persistent credentials).
2. Open the Auth page and run connect for both `bot` and `broadcaster`.
3. Approve both device codes on Twitch.
4. Confirm setup blockers clear and `setup.complete=true`.

## Validation Commands
Strict clean-runtime validation:
```powershell
python scripts/packaged_clean_machine_validation.py `
  --base-url http://127.0.0.1:8787 `
  --username art `
  --password <dashboard_password> `
  --require-readiness-ready `
  --require-gate-enforced `
  --require-setup-complete `
  --require-connected
```

## Test Commands
Full suite:
```powershell
pytest -q
```

Auth/setup suite:
```powershell
pytest -q tests/test_dashboard_api_phase03.py
```

Provider/control/session suite:
```powershell
pytest -q tests/test_provider_abstraction_phase10e.py tests/test_control_room_phase14.py tests/test_session_lifecycle_phase17.py
```

Frontend build check:
```powershell
cd frontend
npm run build
cd ..
```

## Repo Layout
- `src/roonie/` - core runtime, control room, dashboard API
- `src/providers/` - provider routing and key-store logic
- `frontend/` - React dashboard UI (Vite)
- `scripts/` - operational validation tools
- `tests/` - regression and behavior test suites
- `run_control_room.py` - root launcher shim
- `START_ROONIE_LIVE.cmd` - canonical live launcher
- `START_CODEX_FRESH.cmd` - continuity-aware Codex startup helper

## Operational Continuity
Primary continuity vault (Obsidian):
- `D:\OBSIDIAN\AI Projects\ROONIE\CURRENT_STATE.md`
- `D:\OBSIDIAN\AI Projects\ROONIE\10_LOGS_AND_DECISIONS\SESSION_LOG.md`
- `D:\OBSIDIAN\AI Projects\ROONIE\08_WORKPLAN\PRODUCT_BACKLOG.md`
- `D:\OBSIDIAN\AI Projects\ROONIE\DISASTER_RECOVERY.md`
