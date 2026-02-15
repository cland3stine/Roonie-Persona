# Uncommitted Changes Breakdown

Generated: 2026-02-14 07:57:24 -05:00

## Git Status
```text
## main...origin/main
 M live_shim/record_run.py
 M responders/output_gate.py
 M src/roonie/dashboard_api/storage.py
 M src/roonie/offline_director.py
 M src/roonie/offline_responders.py
 M src/roonie/run_control_room.py
 M src/twitch/read_path.py
 M tests/test_dashboard_api_phase03.py
```

## File Summary
| File | Added | Deleted |
|---|---:|---:|
| `live_shim/record_run.py` | 17 | 1 |
| `responders/output_gate.py` | 9 | 1 |
| `src/roonie/dashboard_api/storage.py` | 83 | 0 |
| `src/roonie/offline_director.py` | 27 | 0 |
| `src/roonie/offline_responders.py` | 18 | 1 |
| `src/roonie/run_control_room.py` | 21 | 0 |
| `src/twitch/read_path.py` | 5 | 1 |
| `tests/test_dashboard_api_phase03.py` | 96 | 0 |
| **Total** | **276** | **4** |

## Per-File Breakdown
### `live_shim/record_run.py`
- Added lines: 17
- Deleted lines: 1
- Changed hunks:
  - `@@ -3,0 +4 @@ import json`
  - `@@ -32,0 +34,13 @@ def _git_head_sha() -> str:`
  - `@@ -87 +101,3 @@ def run_payload(payload: dict, emit_outputs: bool = False) -> Path:`

### `responders/output_gate.py`
- Added lines: 9
- Deleted lines: 1
- Changed hunks:
  - `@@ -9,0 +10,8 @@ _LAST_EMIT_TS = 0.0`
  - `@@ -27 +35 @@ def maybe_emit(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:`

### `src/roonie/dashboard_api/storage.py`
- Added lines: 83
- Deleted lines: 0
- Changed hunks:
  - `@@ -3156,0 +3157,83 @@ class DashboardStorage:`

### `src/roonie/offline_director.py`
- Added lines: 27
- Deleted lines: 0
- Changed hunks:
  - `@@ -45,0 +46,20 @@ class OfflineDirector:`
  - `@@ -87,0 +108 @@ class OfflineDirector:`
  - `@@ -101,0 +123 @@ class OfflineDirector:`
  - `@@ -119,0 +142,5 @@ class OfflineDirector:`

### `src/roonie/offline_responders.py`
- Added lines: 18
- Deleted lines: 1
- Changed hunks:
  - `@@ -1 +1 @@`
  - `@@ -22,0 +23,12 @@ _RESPONSES = {`
  - `@@ -240,0 +253,2 @@ def classify_safe_info_category(message: str, profile: Optional[dict] = None) ->`
  - `@@ -383,0 +398,2 @@ def respond(route: str, event: Event, decision: Optional[DecisionRecord]) -> Opt`
  - `@@ -403,0 +420 @@ def respond(route: str, event: Event, decision: Optional[DecisionRecord]) -> Opt`

### `src/roonie/run_control_room.py`
- Added lines: 21
- Deleted lines: 0
- Changed hunks:
  - `@@ -39,0 +40,2 @@ def _arg_parser() -> argparse.ArgumentParser:`
  - `@@ -114,0 +117,16 @@ def main(argv: list[str] | None = None) -> int:`
  - `@@ -125,0 +144,3 @@ def main(argv: list[str] | None = None) -> int:`

### `src/twitch/read_path.py`
- Added lines: 5
- Deleted lines: 1
- Changed hunks:
  - `@@ -54 +54,5 @@ def iter_twitch_messages(`

### `tests/test_dashboard_api_phase03.py`
- Added lines: 96
- Deleted lines: 0
- Changed hunks:
  - `@@ -2184,0 +2185,96 @@ def test_twitch_callback_returns_html_for_browser_accept(tmp_path: Path, monkeyp`

## Notes
- Line ending warning seen by Git: some files will normalize to CRLF on next Git write.
- This report is generated from current working tree diff (unstaged + staged combined vs HEAD).
