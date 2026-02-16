from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roonie.dashboard_api.storage import DashboardStorage
from scripts.log_hygiene_check import SAFE_EVENTSUB_FIELDS, scan_log_paths, validate_eventsub_entry


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))


def test_eventsub_record_omits_unsafe_fields(monkeypatch, tmp_path: Path) -> None:
    _set_dashboard_paths(monkeypatch, tmp_path)
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    storage.record_eventsub_notification(
        twitch_event_id="evt-safe-1",
        event_type="FOLLOW",
        session_id="session-1",
        emitted=False,
        suppression_reason="OUTPUT_DISABLED",
    )
    log_path = tmp_path / "logs" / "eventsub_events.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry.keys()) == SAFE_EVENTSUB_FIELDS
    assert validate_eventsub_entry(entry) == []


def test_hygiene_scanner_flags_unsafe_fields_and_pii_patterns(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    unsafe_entry = {
        "ts": "2026-02-15T00:00:00Z",
        "twitch_event_id": "evt-unsafe-1",
        "event_type": "FOLLOW",
        "session_id": "session-1",
        "emitted": False,
        "suppression_reason": "OUTPUT_DISABLED",
        "display_name": "Alice Example",
        "email": "alice@example.com",
        "ip": "203.0.113.9",
        "oauth_token": "oauth:abcdefghijklmnopqrstuvwxyz",
        "raw_payload": {"event": {"user_name": "Alice"}},
    }
    (logs_dir / "eventsub_events.jsonl").write_text(
        json.dumps(unsafe_entry) + "\n",
        encoding="utf-8",
    )
    report = scan_log_paths([logs_dir / "eventsub_events.jsonl"])
    issues = [row["issue"] for row in report["violations"]]
    assert any(item.startswith("unexpected_fields:") for item in issues)
    assert any(item.startswith("disallowed_fields:") for item in issues)
    assert any(item.startswith("pii_patterns:") for item in issues)


def test_log_hygiene_script_cli_reports_failure_for_unsafe_logs(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "eventsub_events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-02-15T00:00:00Z",
                "twitch_event_id": "evt-unsafe-2",
                "event_type": "FOLLOW",
                "session_id": "session-2",
                "emitted": False,
                "suppression_reason": "OUTPUT_DISABLED",
                "email": "bad@example.com",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cmd = [
        sys.executable,
        str(Path("scripts") / "log_hygiene_check.py"),
        "--logs-dir",
        str(logs_dir),
        "--max-lines",
        "200",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 1
    assert "Log hygiene: FAIL" in output

