from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from roonie.dashboard_api.app import create_server


def _today_ny() -> str:
    try:
        from providers.router import _today_ny as _provider_today_ny

        return _provider_today_ny()
    except Exception:
        pass
    try:
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except ZoneInfoNotFoundError:
        return datetime.utcnow().date().isoformat()


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))


def _write_sample_run(runs_dir: Path) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    sample = {
        "schema_version": "run-v1",
        "session_id": "dash-api-test",
        "director_commit": "abc123",
        "started_at": "2026-02-12T20:00:00+00:00",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hello",
                "actor": "viewer",
                "metadata": {
                    "user": "ruleofrune",
                    "mode": "live",
                    "is_direct_mention": True,
                },
            },
            {
                "event_id": "evt-2",
                "message": "@RoonieTheCat where can I find a job?",
                "actor": "viewer",
                "metadata": {
                    "user": "ruleofrune",
                    "mode": "live",
                    "is_direct_mention": True,
                },
            },
        ],
        "decisions": [
            {
                "case_id": "live",
                "event_id": "evt-1",
                "action": "NOOP",
                "route": "none",
                "response_text": None,
                "trace": {
                    "gates": {"addressed_to_roonie": True},
                    "policy": {"refusal_reason_code": None},
                    "routing": {"routing_reason_codes": []},
                },
                "context_active": False,
                "context_turns_used": 0,
            },
            {
                "case_id": "live",
                "event_id": "evt-2",
                "action": "RESPOND_PUBLIC",
                "route": "primary:openai",
                "response_text": "Try local job boards first.",
                "trace": {
                    "gates": {"addressed_to_roonie": True},
                    "policy": {"refusal_reason_code": None},
                    "routing": {"routing_reason_codes": ["ROUTE_SAFE_INFO"]},
                },
                "context_active": True,
                "context_turns_used": 2,
            },
        ],
        "outputs": [
            {"event_id": "evt-1", "emitted": False, "reason": "ACTION_NOT_ALLOWED", "sink": "stdout"},
            {"event_id": "evt-2", "emitted": False, "reason": "RATE_LIMIT", "sink": "stdout"},
        ],
    }
    (runs_dir / "run.json").write_text(json.dumps(sample), encoding="utf-8")


def _sample_memory_intent_run(
    *,
    session_id: str = "memory-intent-session",
    event_id: str = "evt-memory-1",
    user: str = "RuleOfRune",
    preference: str = "like",
    memory_object: str = "progressive house",
) -> Dict[str, Any]:
    return {
        "schema_version": "run-v1",
        "session_id": session_id,
        "started_at": "2026-02-16T00:00:00+00:00",
        "inputs": [
            {
                "event_id": event_id,
                "message": f"I {preference} {memory_object}",
                "metadata": {"user": user, "mode": "live", "is_direct_mention": True},
            }
        ],
        "decisions": [
            {
                "case_id": "live",
                "event_id": event_id,
                "action": "MEMORY_WRITE_INTENT",
                "route": "none",
                "response_text": None,
                "trace": {
                    "memory_intent": {
                        "scope": "viewer",
                        "user": user,
                        "preference": preference,
                        "object": memory_object,
                        "confidence": 0.9,
                        "ttl_days": 180,
                        "cue": f"i {preference}",
                    }
                },
            }
        ],
        "outputs": [],
    }


def _get_json(base: str, path: str) -> Dict[str, Any] | list[Dict[str, Any]]:
    with urllib.request.urlopen(f"{base}{path}", timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
) -> tuple[int, Dict[str, Any] | list[Dict[str, Any]]]:
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers=req_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _request_json_with_headers(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
) -> tuple[int, Dict[str, Any] | list[Dict[str, Any]], Dict[str, str]]:
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers=req_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return (
                response.status,
                json.loads(response.read().decode("utf-8")),
                dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            json.loads(exc.read().decode("utf-8")),
            dict(exc.headers.items()),
        )


def _request_bytes_with_headers(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
) -> tuple[int, bytes, Dict[str, str]]:
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers=req_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def _request_multipart_json(
    base: str,
    path: str,
    *,
    filename: str,
    content: bytes,
    field_name: str = "file",
    headers: Dict[str, str] | None = None,
) -> tuple[int, Dict[str, Any] | list[Dict[str, Any]]]:
    boundary = "----RoonieBoundaryD3"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        "Content-Type: application/xml\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req_headers = dict(headers or {})
    req_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method="POST",
        headers=req_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _cookie_from_response_headers(headers: Dict[str, str]) -> str:
    raw = str(headers.get("Set-Cookie", "")).strip()
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip()


def _set_cookie_header(headers: Dict[str, str]) -> str:
    for key, value in headers.items():
        if str(key).lower() == "set-cookie":
            return str(value)
    return ""


def _read_memory_audit_rows(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, ts, username, role, auth_mode, action, table_name, row_id, before_hash, after_hash, diff_summary
            FROM memory_audit
            ORDER BY ts DESC
            """
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _memory_row_exists(db_path: Path, table_name: str, row_id: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            f"SELECT 1 FROM {table_name} WHERE id = ? LIMIT 1",
            (row_id,),
        ).fetchone()
    return row is not None


def _start_server(runs_dir: Path):
    server = create_server(host="127.0.0.1", port=0, runs_dir=runs_dir)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return server, thread


def test_dashboard_api_status_structure(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    monkeypatch.setenv("ROONIE_POLICY_VERSION", "p-1")
    _set_dashboard_paths(monkeypatch, tmp_path)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        data = _get_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    expected = {
        "kill_switch_on",
        "armed",
        "mode",
        "twitch_connected",
        "last_heartbeat_at",
        "active_provider",
        "version",
        "policy_loaded_at",
        "policy_version",
        "context_last_active",
        "context_last_turns_used",
    }
    assert expected.issubset(set(data.keys()))
    assert data["active_provider"] == "openai"
    assert data["context_last_active"] is True
    assert data["context_last_turns_used"] == 2


def test_dashboard_api_events_and_suppressions(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        events = _get_json(base, "/api/events?limit=5")
        suppressions = _get_json(base, "/api/suppressions?limit=5")
        operator_log = _get_json(base, "/api/operator_log?limit=5")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert isinstance(events, list)
    assert len(events) == 2
    for key in {
        "ts",
        "user_handle",
        "message_text",
        "direct_address",
        "decision_type",
        "final_text",
        "decision",
        "suppression_reason",
        "suppression_detail",
        "context_active",
        "context_turns_used",
    }:
        assert key in events[0]

    assert isinstance(suppressions, list)
    assert len(suppressions) == 2
    assert suppressions[0]["suppression_reason"] == "RATE_LIMIT"
    assert isinstance(operator_log, list)
    assert operator_log == []


def test_dashboard_api_ignores_malformed_latest_run_file(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    # Force parse-attempt behavior to prove malformed JSON is skipped by decoder safety.
    monkeypatch.setenv("ROONIE_DASHBOARD_RECENT_FILE_GRACE_SECONDS", "0")
    _set_dashboard_paths(monkeypatch, tmp_path)
    (runs_dir / "zzz_partial.json").write_text('{"schema_version": "run-v1", "inputs": [', encoding="utf-8")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status = _get_json(base, "/api/status")
        events = _get_json(base, "/api/events?limit=5")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status["active_provider"] == "openai"
    assert isinstance(events, list)
    assert len(events) == 2


def test_write_endpoints_forbidden_when_operator_key_missing(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status_code, body = _request_json(base, "/api/live/arm", method="POST", payload={"actor": "Art"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status_code == 403
    assert body["ok"] is False
    assert "READ-ONLY" in body["detail"]


def test_write_endpoints_and_audit_with_operator_key(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"

        code_arm, arm_body = _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        assert code_arm == 200
        assert arm_body["ok"] is True
        assert arm_body["state"]["armed"] is True

        _, status_after_arm = _request_json(base, "/api/status")
        assert status_after_arm["armed"] is True
        assert status_after_arm["read_only_mode"] is False
        assert status_after_arm["can_post"] is True
        assert status_after_arm["blocked_by"] == []

        code_silence, silence_body = _request_json(
            base,
            "/api/live/silence_now",
            method="POST",
            payload={"ttl_seconds": 300},
            headers=headers,
        )
        assert code_silence == 200
        assert silence_body["state"]["silenced"] is True

        _, status_after_silence = _request_json(base, "/api/status")
        assert status_after_silence["silenced"] is True
        assert status_after_silence["can_post"] is False
        assert status_after_silence["blocked_by"] == ["SILENCE_TTL"]

        code_disarm, disarm_body = _request_json(base, "/api/live/disarm", method="POST", payload={}, headers=headers)
        assert code_disarm == 200
        assert disarm_body["state"]["armed"] is False

        _, status_after_disarm = _request_json(base, "/api/status")
        assert status_after_disarm["armed"] is False
        assert status_after_disarm["blocked_by"] == ["DISARMED"]
        assert status_after_disarm["can_post"] is False

        code_cancel, cancel_body = _request_json(
            base,
            "/api/queue/cancel",
            method="POST",
            payload={"id": "missing-id"},
            headers=headers,
        )
        assert code_cancel == 200
        assert cancel_body["result"] == "NOT_FOUND"

        _, op_log = _request_json(base, "/api/operator_log?limit=10")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    actions = [item["action"] for item in op_log]
    assert "CONTROL_ARM_SET" in actions
    assert "SILENCE_NOW" in actions
    assert "CONTROL_DISARM_SET" in actions
    assert op_log[0]["actor"] == "jen"
    arm_entry = next(item for item in op_log if item["action"] == "CONTROL_ARM_SET")
    assert arm_entry["auth_mode"] == "legacy_key"


def test_operator_actor_defaults_to_unknown_when_missing(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")

    headers = {"X-ROONIE-OP-KEY": "op-key-123"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        assert code == 200
        assert body["ok"] is True
        _, op_log = _request_json(base, "/api/operator_log?limit=1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert len(op_log) == 1
    assert op_log[0]["actor"] == "unknown"


def test_status_blocked_by_precedence_order(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "1")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "system"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _request_json(base, "/api/live/silence_now", method="POST", payload={"ttl_seconds": 120}, headers=headers)
        _, status = _request_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status["blocked_by"] == ["SILENCE_TTL"]
    assert status["can_post"] is False


def test_arm_status_no_longer_depends_on_implicit_kill_switch(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.delenv("ROONIE_KILL_SWITCH", raising=False)
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    monkeypatch.delenv("ROONIE_KILL_SWITCH_ON", raising=False)
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, status_before = _request_json(base, "/api/status")
        _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _, status_after_arm = _request_json(base, "/api/status")
        _request_json(base, "/api/live/disarm", method="POST", payload={}, headers=headers)
        _, status_after_disarm = _request_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status_before["kill_switch_on"] is False
    assert status_before["blocked_by"] == ["DISARMED"]
    assert status_after_arm["kill_switch_on"] is False
    assert status_after_arm["can_post"] is True
    assert status_after_arm["blocked_by"] == []
    assert status_after_disarm["kill_switch_on"] is False
    assert status_after_disarm["blocked_by"] == ["DISARMED"]


def test_studio_profile_get_creates_defaults_when_missing(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    profile_path = tmp_path / "data" / "studio_profile.json"
    assert not profile_path.exists()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        profile = _get_json(base, "/api/studio_profile")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert profile_path.exists()
    assert profile["version"] == 1
    assert profile["location"]["display"] == "Washington DC area"
    assert isinstance(profile["social_links"], list)
    assert isinstance(profile["gear"], list)
    assert isinstance(profile["faq"], list)
    assert isinstance(profile["approved_emotes"], list)


def test_studio_profile_write_forbidden_without_operator_key(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    payload = {"location": {"display": "Washington DC area"}}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        put_code, put_body = _request_json(base, "/api/studio_profile", method="PUT", payload=payload)
        patch_code, patch_body = _request_json(base, "/api/studio_profile", method="PATCH", payload=payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert put_code == 403
    assert put_body["ok"] is False
    assert patch_code == 403
    assert patch_body["ok"] is False


def test_studio_profile_write_success_audited_and_atomic(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "art"}
    profile_path = tmp_path / "data" / "studio_profile.json"

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, original = _request_json(base, "/api/studio_profile")
        updated = dict(original)
        updated["location"] = {"display": "Washington DC metro area"}
        put_code, put_body = _request_json(base, "/api/studio_profile", method="PUT", payload=updated, headers=headers)
        _, op_log = _request_json(base, "/api/operator_log?limit=10")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert put_code == 200
    assert put_body["ok"] is True
    assert put_body["profile"]["location"]["display"] == "Washington DC metro area"
    assert profile_path.exists()
    assert json.loads(profile_path.read_text(encoding="utf-8"))["location"]["display"] == "Washington DC metro area"
    assert not list((tmp_path / "data").glob("*.tmp"))
    assert any(item["action"] == "STUDIO_PROFILE_UPDATE" for item in op_log)
    latest_update = next(item for item in op_log if item["action"] == "STUDIO_PROFILE_UPDATE")
    assert latest_update["actor"] == "art"
    summary = latest_update.get("payload_summary") or ""
    assert "old_snapshot_hash" in summary
    assert "new_snapshot_hash" in summary


def test_studio_profile_validation_rejects_bad_url(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, original = _request_json(base, "/api/studio_profile")
        bad = dict(original)
        bad["social_links"] = [{"label": "Twitch", "url": "twitch.tv/ruleofrune"}]
        code, body = _request_json(base, "/api/studio_profile", method="PUT", payload=bad, headers=headers)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 400
    assert body["ok"] is False
    assert "url" in body["detail"].lower()


def test_library_upload_forbidden_without_operator_key(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    xml_bytes = Path("tests/fixtures/v1_13_library/rekordbox_sample.xml").read_bytes()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_multipart_json(
            base,
            "/api/library_index/upload_xml",
            filename="rekordbox.xml",
            content=xml_bytes,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 403
    assert body["ok"] is False


def test_library_rebuild_and_search_tiers_with_audit(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "art"}
    xml_bytes = Path("tests/fixtures/v1_13_library/rekordbox_sample.xml").read_bytes()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        upload_code, upload_body = _request_multipart_json(
            base,
            "/api/library_index/upload_xml",
            filename="rekordbox.xml",
            content=xml_bytes,
            headers=headers,
        )
        rebuild_code, rebuild_body = _request_json(
            base,
            "/api/library_index/rebuild",
            method="POST",
            payload={},
            headers=headers,
        )
        _, status = _request_json(base, "/api/library_index/status")
        _, exact = _request_json(base, "/api/library_index/search?q=Guy%20J%20-%20Lamur")
        _, close = _request_json(base, "/api/library_index/search?q=Guy%20J%20Lamur%20remix")
        _, none = _request_json(base, "/api/library_index/search?q=Nope%20Artist%20-%20Missing")
        _, op_log = _request_json(base, "/api/operator_log?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert upload_code == 200
    assert upload_body["ok"] is True
    assert rebuild_code == 200
    assert rebuild_body["ok"] is True
    assert status["track_count"] == 3
    assert status["build_ok"] is True

    assert exact["confidence"] == "EXACT"
    assert len(exact["matches"]) >= 1
    assert close["confidence"] == "CLOSE"
    assert len(close["matches"]) >= 1
    assert none["confidence"] == "NONE"
    assert none["matches"] == []

    actions = [item["action"] for item in op_log]
    assert "LIBRARY_XML_UPLOAD" in actions
    assert "LIBRARY_INDEX_REBUILD" in actions


def test_logs_events_endpoint_limit_and_filters(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, events_l1 = _request_json(base, "/api/logs/events?limit=1&offset=0")
        _, events_q = _request_json(base, "/api/logs/events?limit=20&q=ruleofrune")
        _, events_sup = _request_json(base, "/api/logs/events?limit=20&suppression_reason=RATE_LIMIT")
        _, events_by_type = _request_json(base, "/api/logs/events?limit=20&decision_type=suppress")
        _, suppressions = _request_json(base, "/api/logs/suppressions?limit=20&suppression_reason=RATE_LIMIT")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert "items" in events_l1 and isinstance(events_l1["items"], list)
    assert len(events_l1["items"]) == 1
    assert events_l1["total_count"] >= 2

    assert events_q["total_count"] >= 2
    assert all("ruleofrune" in str(item.get("user_handle", "")).lower() for item in events_q["items"])

    assert events_sup["total_count"] == 1
    assert len(events_sup["items"]) == 1
    assert events_sup["items"][0]["suppression_reason"] == "RATE_LIMIT"
    assert events_sup["items"][0]["decision_type"] == "suppress"
    assert events_sup["items"][0]["decision"] == events_sup["items"][0]["final_text"]

    assert events_by_type["total_count"] == 2
    assert all(item["decision_type"] == "suppress" for item in events_by_type["items"])

    assert suppressions["total_count"] == 1
    assert suppressions["items"][0]["suppression_reason"] == "RATE_LIMIT"


def test_logs_operator_endpoint_filters(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _request_json(base, "/api/live/disarm", method="POST", payload={}, headers=headers)
        _, logs_arm = _request_json(base, "/api/logs/operator?limit=20&action=ARM")
        _, logs_actor = _request_json(base, "/api/logs/operator?limit=20&actor=jen")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert logs_arm["total_count"] >= 1
    assert any("ARM" in str(item.get("action", "")) for item in logs_arm["items"])
    assert logs_actor["total_count"] >= 2
    assert all(str(item.get("actor", "")) == "jen" for item in logs_actor["items"])


def test_logs_event_legacy_decision_alias_matches_final_text(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, body = _request_json(base, "/api/logs/events?limit=10&offset=0")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert isinstance(body.get("items"), list)
    assert len(body["items"]) >= 1
    for item in body["items"]:
        assert item.get("decision") == item.get("final_text")


def test_providers_status_seeds_defaults_when_missing(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    providers_path = tmp_path / "data" / "providers_config.json"
    assert not providers_path.exists()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status = _get_json(base, "/api/providers/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert providers_path.exists()
    assert status["active_provider"] == "openai"
    assert status["approved_providers"] == ["openai", "grok"]
    assert status["caps"]["daily_requests_max"] == 500
    assert status["usage"]["requests"] == 0


def test_providers_set_active_forbidden_without_operator_key(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_json(
            base,
            "/api/providers/set_active",
            method="POST",
            payload={"provider": "grok"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 403
    assert body["ok"] is False


def test_providers_set_active_rejects_unapproved_provider(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_json(
            base,
            "/api/providers/set_active",
            method="POST",
            payload={"provider": "anthropic"},
            headers=headers,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 400
    assert body["ok"] is False
    assert "approved_providers" in str(body.get("detail", ""))


def test_providers_set_active_and_caps_are_audited(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "art"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code_set, set_body = _request_json(
            base,
            "/api/providers/set_active",
            method="POST",
            payload={"provider": "grok"},
            headers=headers,
        )
        code_caps, caps_body = _request_json(
            base,
            "/api/providers/caps",
            method="PATCH",
            payload={"daily_requests_max": 3, "hard_stop_on_cap": True},
            headers=headers,
        )
        _, provider_status = _request_json(base, "/api/providers/status")
        _, op_log = _request_json(base, "/api/operator_log?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_set == 200
    assert set_body["ok"] is True
    assert code_caps == 200
    assert caps_body["ok"] is True
    assert provider_status["active_provider"] == "grok"
    assert provider_status["caps"]["daily_requests_max"] == 3

    actions = [item["action"] for item in op_log]
    assert "PROVIDER_SET_ACTIVE" in actions
    assert "PROVIDER_SET_CAPS" in actions
    latest_provider_action = next(item for item in op_log if item["action"] == "PROVIDER_SET_ACTIVE")
    assert latest_provider_action["actor"] == "art"


def test_status_blocked_by_cost_cap_when_limit_reached(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")
    providers_path = tmp_path / "data" / "providers_config.json"
    providers_path.parent.mkdir(parents=True, exist_ok=True)
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 1,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {
                    "day": _today_ny(),
                    "requests": 1,
                    "tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _, status = _request_json(base, "/api/status")
        _, providers = _request_json(base, "/api/providers/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert "COST_CAP" not in status["blocked_by"]
    assert status["can_post"] is True
    assert "COST_CAP" not in providers["blocked_by"]
    assert providers["can_post"] is True


def test_route_generate_enforces_cost_cap_and_sets_suppression_reason(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import route_generate

    providers_path = tmp_path / "providers_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 1,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {
                    "day": _today_ny(),
                    "requests": 0,
                    "tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))

    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "anthropic": {"enabled": False},
                "grok": {"enabled": False},
            },
        }
    )

    first_context = {"use_provider_config": True}
    out1 = route_generate(registry=reg, routing_cfg={}, prompt="ping", context=first_context)
    assert out1 == "[openai stub] ping"

    first_cfg = json.loads(providers_path.read_text(encoding="utf-8"))
    assert first_cfg["usage"]["requests"] == 1

    second_context = {"use_provider_config": True}
    out2 = route_generate(registry=reg, routing_cfg={}, prompt="ping-2", context=second_context)
    assert out2 is None
    assert second_context["suppression_reason"] == "COST_CAP"
    assert second_context["provider_block_reason"] == "COST_CAP"

    second_cfg = json.loads(providers_path.read_text(encoding="utf-8"))
    assert second_cfg["usage"]["requests"] == 1


def test_routing_default_on_routes_music_to_grok(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import route_generate

    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 10,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {
                    "day": _today_ny(),
                    "requests": 0,
                    "tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))

    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )

    context = {"use_provider_config": True, "message_text": "what track is this tune?"}
    out = route_generate(registry=reg, routing_cfg={}, prompt="ping", context=context)
    assert out == "[grok stub] ping"
    assert context["routing_enabled"] is True
    assert context["routing_class"] == "music_culture"
    assert context["provider_selected"] == "grok"
    assert context["moderation_result"] == "allow"


def test_routing_enabled_music_selects_grok_and_runs_moderation(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import route_generate

    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 10,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {
                    "day": _today_ny(),
                    "requests": 0,
                    "tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    routing_path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "default_provider": "openai",
                "music_route_provider": "grok",
                "moderation_provider": "openai",
                "manual_override": "default",
                "classification_rules": {
                    "music_culture_keywords": ["track", "id"],
                    "artist_title_pattern": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))

    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )

    context = {
        "use_provider_config": True,
        "message_text": "track id please",
        "utility_source": "utility_track_id",
    }
    out = route_generate(registry=reg, routing_cfg={}, prompt="ping", context=context)
    assert out == "[grok stub] ping"
    assert context["routing_enabled"] is True
    assert context["provider_selected"] == "grok"
    assert context["moderation_provider_used"] == "openai"
    assert context["moderation_result"] == "allow"


def test_routing_grok_blocked_by_openai_moderation_sets_suppression_reason(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import route_generate

    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 10,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {
                    "day": _today_ny(),
                    "requests": 0,
                    "tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    routing_path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "default_provider": "openai",
                "music_route_provider": "grok",
                "moderation_provider": "openai",
                "manual_override": "default",
                "classification_rules": {
                    "music_culture_keywords": ["track", "id"],
                    "artist_title_pattern": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))

    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )

    context = {
        "use_provider_config": True,
        "message_text": "track id please",
        "utility_source": "library_index",
    }
    out = route_generate(
        registry=reg,
        routing_cfg={},
        prompt="ping",
        context=context,
        test_overrides={"moderation_behavior": "block"},
    )
    assert out is None
    assert context["provider_selected"] == "grok"
    assert context["moderation_result"] == "block"
    assert context["suppression_reason"] == "MODERATION_BLOCK"
    assert context["provider_block_reason"] == "MODERATION_BLOCK"


def test_routing_config_patch_is_director_only_and_audited(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        no_auth_code, _ = _request_json(base, "/api/routing/status")
        _, _, jen_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        jen_cookie = _cookie_from_response_headers(jen_headers)
        jen_h = {"Cookie": jen_cookie}
        jen_code, _ = _request_json(
            base,
            "/api/routing/config",
            method="PATCH",
            payload={"enabled": True},
            headers=jen_h,
        )

        _, _, art_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        art_cookie = _cookie_from_response_headers(art_headers)
        art_h = {"Cookie": art_cookie}
        art_code, art_body = _request_json(
            base,
            "/api/routing/config",
            method="PATCH",
            payload={"enabled": True, "manual_override": "force_grok"},
            headers=art_h,
        )
        _, routing_status = _request_json(base, "/api/routing/status", headers=art_h)
        _, op_log = _request_json(base, "/api/operator_log?limit=20", headers=art_h)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert no_auth_code == 403
    assert jen_code == 403
    assert art_code == 200
    assert art_body["ok"] is True
    assert routing_status["enabled"] is True
    assert routing_status["manual_override"] == "force_grok"
    routing_action = next(item for item in op_log if item["action"] == "ROUTING_CONFIG_UPDATE")
    assert routing_action["username"] == "art"
    assert routing_action["role"] == "director"
    assert routing_action["auth_mode"] == "session"


def test_control_routing_and_director_endpoints_toggle_and_audit(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, initial_status = _request_json(base, "/api/status")
        code_route, body_route = _request_json(
            base,
            "/control/routing",
            method="POST",
            payload={"enabled": False},
            headers=headers,
        )
        code_director, body_director = _request_json(
            base,
            "/control/director",
            method="POST",
            payload={"active": "OfflineDirector"},
            headers=headers,
        )
        _, status = _request_json(base, "/api/status")
        _, op_log = _request_json(base, "/api/operator_log?limit=50")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_route == 200
    assert body_route["ok"] is True
    assert body_route["status"]["enabled"] is False
    assert code_director == 200
    assert body_director["ok"] is True
    assert initial_status["routing_enabled"] is True
    assert initial_status["active_director"] == "ProviderDirector"
    assert status["routing_enabled"] is False
    assert status["active_director"] == "OfflineDirector"
    assert any(item.get("action") == "CONTROL_ROUTING_SET" for item in op_log)
    assert any(item.get("action") == "CONTROL_DIRECTOR_SET" for item in op_log)


def test_control_dry_run_endpoint_toggles_and_status_reflects(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.delenv("ROONIE_DRY_RUN", raising=False)
    monkeypatch.delenv("ROONIE_READ_ONLY_MODE", raising=False)

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, initial_status = _request_json(base, "/api/status")
        assert initial_status["read_only_mode"] is False
        code_on, body_on = _request_json(
            base,
            "/control/dry_run",
            method="POST",
            payload={"enabled": True},
            headers=headers,
        )
        assert code_on == 200
        assert body_on["ok"] is True
        _, status_on = _request_json(base, "/api/status")
        assert status_on["read_only_mode"] is True
        code_off, body_off = _request_json(
            base,
            "/control/dry_run",
            method="POST",
            payload={"enabled": False},
            headers=headers,
        )
        assert code_off == 200
        assert body_off["ok"] is True
        _, status_off = _request_json(base, "/api/status")
        assert status_off["read_only_mode"] is False
        _, op_log = _request_json(base, "/api/operator_log?limit=50")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert any(item.get("action") == "CONTROL_DRY_RUN_SET" for item in op_log)


def test_routing_off_never_selects_grok_even_if_active_provider_is_grok(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import route_generate

    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "grok",
                "approved_providers": ["openai", "grok"],
                "caps": {"daily_requests_max": 10, "daily_tokens_max": 0, "hard_stop_on_cap": True},
                "usage": {"day": _today_ny(), "requests": 0, "tokens": 0},
            }
        ),
        encoding="utf-8",
    )
    routing_path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": False,
                "default_provider": "grok",
                "music_route_provider": "grok",
                "moderation_provider": "openai",
                "manual_override": "force_grok",
                "classification_rules": {"music_culture_keywords": ["track", "id"], "artist_title_pattern": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))

    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )
    context = {"use_provider_config": True, "message_text": "track id please"}
    out = route_generate(registry=reg, routing_cfg={}, prompt="ping", context=context)
    assert out == "[openai stub] ping"
    assert context["routing_enabled"] is False
    assert context["provider_selected"] == "openai"


def test_provider_runtime_metrics_increment_for_requests_and_routing_hits(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import (
        get_provider_runtime_metrics,
        reset_provider_runtime_metrics_for_tests,
        route_generate,
    )

    reset_provider_runtime_metrics_for_tests()
    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 25,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {"day": _today_ny(), "requests": 0, "tokens": 0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )

    out1 = route_generate(
        registry=reg,
        routing_cfg={},
        prompt="hello",
        context={"use_provider_config": True, "message_text": "hello there"},
    )
    out2 = route_generate(
        registry=reg,
        routing_cfg={},
        prompt="track",
        context={"use_provider_config": True, "message_text": "track id please"},
    )
    assert out1 == "[openai stub] hello"
    assert out2 == "[grok stub] track"

    metrics = get_provider_runtime_metrics()
    openai = metrics["providers"]["openai"]
    grok = metrics["providers"]["grok"]
    assert openai["requests"] == 1
    assert openai["success"] == 1
    assert openai["failures"] == 0
    assert openai["avg_latency_ms"] >= 0
    assert grok["requests"] == 1
    assert grok["success"] == 1
    assert grok["failures"] == 0
    assert grok["avg_latency_ms"] >= 0
    assert metrics["routing"]["general_hits"] == 1
    assert metrics["routing"]["music_culture_hits"] == 1


def test_provider_runtime_metrics_track_moderation_block(tmp_path: Path, monkeypatch) -> None:
    from providers.registry import ProviderRegistry
    from providers.router import (
        get_provider_runtime_metrics,
        reset_provider_runtime_metrics_for_tests,
        route_generate,
    )

    reset_provider_runtime_metrics_for_tests()
    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 25,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {"day": _today_ny(), "requests": 0, "tokens": 0},
            }
        ),
        encoding="utf-8",
    )
    routing_path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "default_provider": "openai",
                "music_route_provider": "grok",
                "moderation_provider": "openai",
                "manual_override": "default",
                "classification_rules": {
                    "music_culture_keywords": ["track", "id"],
                    "artist_title_pattern": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    reg = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )

    out = route_generate(
        registry=reg,
        routing_cfg={},
        prompt="blocked",
        context={"use_provider_config": True, "message_text": "track id please", "utility_source": "library_index"},
        test_overrides={"moderation_behavior": "block"},
    )
    assert out is None
    metrics = get_provider_runtime_metrics()
    grok = metrics["providers"]["grok"]
    assert grok["requests"] == 1
    assert grok["moderation_blocks"] == 1


def test_system_health_endpoint_structure_and_memory_reachable(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        code, body = _request_json(base, "/api/system/health", headers={"Cookie": cookie})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 200
    assert "providers" in body
    assert "routing" in body
    assert "memory_db" in body
    assert body["memory_db"]["reachable"] is True
    assert isinstance(body["memory_db"]["file_size_bytes"], int)
    assert isinstance(body["providers_config_present"], bool)
    assert isinstance(body["routing_config_present"], bool)
    assert "last_error" in body


def test_system_export_endpoint_director_only_and_zip_contents(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"

        _, _, jen_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        jen_cookie = _cookie_from_response_headers(jen_headers)
        op_code, op_body_bytes, _ = _request_bytes_with_headers(
            base,
            "/api/system/export",
            headers={"Cookie": jen_cookie},
        )

        _, _, art_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        art_cookie = _cookie_from_response_headers(art_headers)
        dir_code, dir_zip, dir_headers = _request_bytes_with_headers(
            base,
            "/api/system/export",
            headers={"Cookie": art_cookie},
        )
        _, op_log = _request_json(base, "/api/operator_log?limit=20", headers={"Cookie": art_cookie})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert op_code == 403
    op_body = json.loads(op_body_bytes.decode("utf-8"))
    assert op_body["ok"] is False

    assert dir_code == 200
    assert "application/zip" in str(dir_headers.get("Content-Type", "")).lower()
    with zipfile.ZipFile(io.BytesIO(dir_zip), mode="r") as zf:
        names = sorted(zf.namelist())
    expected = sorted(
        [
            "data/providers_config.json",
            "data/routing_config.json",
            "data/studio_profile.json",
            "data/senses_config.json",
            "data/twitch_config.json",
            "data/memory.sqlite",
        ]
    )
    assert names == expected
    export_log = next(item for item in op_log if item["action"] == "SYSTEM_EXPORT")
    assert export_log["username"] == "art"
    assert export_log["role"] == "director"
    assert export_log["auth_mode"] == "session"


def test_auth_login_me_logout_flow(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_SESSION_TTL_SECONDS", "43200")
    monkeypatch.setenv("ROONIE_DASHBOARD_SECURE_COOKIES", "0")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, me_before = _request_json(base, "/api/auth/me")
        bad_code, bad_login, _ = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "wrong"},
        )
        ok_code, ok_login, headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(headers)
        login_set_cookie = _set_cookie_header(headers)
        _, me_after = _request_json(base, "/api/auth/me", headers={"Cookie": cookie})
        logout_code, logout_body, logout_headers = _request_json_with_headers(
            base,
            "/api/auth/logout",
            method="POST",
            payload={},
            headers={"Cookie": cookie},
        )
        logout_set_cookie = _set_cookie_header(logout_headers)
        _, me_final = _request_json(base, "/api/auth/me", headers={"Cookie": cookie})
        code_after_logout, write_after_logout = _request_json(
            base,
            "/api/live/arm",
            method="POST",
            payload={},
            headers={"Cookie": cookie},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert me_before["authenticated"] is False
    assert bad_code == 401
    assert bad_login["ok"] is False
    assert ok_code == 200
    assert ok_login["authenticated"] is True
    assert cookie.startswith("roonie_session=")
    assert "HttpOnly" in login_set_cookie
    assert "SameSite=Lax" in login_set_cookie
    assert "Path=/" in login_set_cookie
    assert "Max-Age=43200" in login_set_cookie
    assert "Secure" not in login_set_cookie
    assert me_after["authenticated"] is True
    assert me_after["username"] == "jen"
    assert me_after["role"] == "operator"
    assert logout_code == 200
    assert logout_body["authenticated"] is False
    assert "HttpOnly" in logout_set_cookie
    assert "SameSite=Lax" in logout_set_cookie
    assert "Path=/" in logout_set_cookie
    assert "Max-Age=0" in logout_set_cookie
    assert me_final["authenticated"] is False
    assert code_after_logout == 403
    assert write_after_logout["ok"] is False


def test_auth_login_set_cookie_secure_flag_toggle(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_SECURE_COOKIES", "1")
    monkeypatch.setenv("ROONIE_DASHBOARD_SESSION_TTL_SECONDS", "43200")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body, headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        set_cookie = _set_cookie_header(headers)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 200
    assert body["authenticated"] is True
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=43200" in set_cookie
    assert "Secure" in set_cookie


def test_auth_session_expires_after_ttl(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_SESSION_TTL_SECONDS", "1")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code_login, login_body, headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(headers)
        me_after = {"authenticated": True}
        for _ in range(20):
            _, me_after = _request_json(base, "/api/auth/me", headers={"Cookie": cookie})
            if me_after.get("authenticated") is False:
                break
            time.sleep(0.1)
        code_write, write_body = _request_json(
            base,
            "/api/live/arm",
            method="POST",
            payload={},
            headers={"Cookie": cookie},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_login == 200
    assert login_body["authenticated"] is True
    assert me_after["authenticated"] is False
    assert code_write == 403
    assert write_body["ok"] is False


def test_write_requires_auth_without_session_or_operator_key(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_json(base, "/api/live/arm", method="POST", payload={})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 403
    assert body["ok"] is False


def test_operator_session_permissions_and_audit_identity(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    xml_bytes = Path("tests/fixtures/v1_13_library/rekordbox_sample.xml").read_bytes()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        cookie_headers = {"Cookie": cookie}

        code_arm, _ = _request_json(base, "/api/live/arm", method="POST", payload={}, headers=cookie_headers)
        _, profile = _request_json(base, "/api/studio_profile")
        updated = dict(profile)
        updated["location"] = {"display": "Washington DC area"}
        code_profile, _ = _request_json(
            base,
            "/api/studio_profile",
            method="PUT",
            payload=updated,
            headers=cookie_headers,
        )
        code_upload, _ = _request_multipart_json(
            base,
            "/api/library_index/upload_xml",
            filename="rekordbox.xml",
            content=xml_bytes,
            headers=cookie_headers,
        )
        code_rebuild, _ = _request_json(
            base,
            "/api/library_index/rebuild",
            method="POST",
            payload={},
            headers=cookie_headers,
        )
        code_active, _ = _request_json(
            base,
            "/api/providers/set_active",
            method="POST",
            payload={"provider": "grok"},
            headers=cookie_headers,
        )
        code_caps_ok, _ = _request_json(
            base,
            "/api/providers/caps",
            method="PATCH",
            payload={"daily_requests_max": 1200},
            headers=cookie_headers,
        )
        code_caps_forbidden, caps_forbidden = _request_json(
            base,
            "/api/providers/caps",
            method="PATCH",
            payload={"hard_stop_on_cap": False},
            headers=cookie_headers,
        )
        _, op_log = _request_json(base, "/api/operator_log?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_arm == 200
    assert code_profile == 200
    assert code_upload == 200
    assert code_rebuild == 200
    assert code_active == 200
    assert code_caps_ok == 200
    assert code_caps_forbidden == 403
    assert caps_forbidden["ok"] is False
    assert "director" in str(caps_forbidden.get("detail", "")).lower()

    provider_caps_entry = next(item for item in op_log if item["action"] == "PROVIDER_SET_CAPS")
    assert provider_caps_entry["username"] == "jen"
    assert provider_caps_entry["role"] == "operator"
    assert provider_caps_entry["auth_mode"] == "session"
    arm_entry = next(item for item in op_log if item["action"] == "CONTROL_ARM_SET")
    assert arm_entry["auth_mode"] == "session"


def test_director_can_update_hard_stop_cap_and_twitch_auth_endpoints(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "oauth:test-bot-token")
    monkeypatch.setenv("TWITCH_BROADCASTER_OAUTH_TOKEN", "oauth:test-broadcaster-token")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")
    monkeypatch.setenv("TWITCH_TOKEN_SCOPES", "chat:read chat:edit")
    monkeypatch.setenv("TWITCH_TOKEN_EXPIRES_AT", "2026-02-12T23:59:59+00:00")
    monkeypatch.setenv("TWITCH_LAST_ERROR", "")
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        cookie_headers = {"Cookie": cookie}
        code_caps, caps_body = _request_json(
            base,
            "/api/providers/caps",
            method="PATCH",
            payload={"hard_stop_on_cap": False, "daily_requests_max": 7000},
            headers=cookie_headers,
        )
        _, twitch_status = _request_json(base, "/api/twitch/status")
        reconnect_code, reconnect_body = _request_json(
            base,
            "/api/auth/twitch_reconnect",
            method="POST",
            payload={"account": "bot"},
            headers=cookie_headers,
        )
        connect_code, connect_body = _request_json(
            base,
            "/api/twitch/connect_start",
            method="POST",
            payload={"account": "broadcaster"},
            headers=cookie_headers,
        )
        disconnect_code, disconnect_body = _request_json(
            base,
            "/api/twitch/disconnect",
            method="POST",
            payload={"account": "bot"},
            headers=cookie_headers,
        )
        _, op_log = _request_json(base, "/api/operator_log?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_caps == 200
    assert caps_body["ok"] is True
    assert twitch_status["scopes_present"]["chat:read"] is True
    assert twitch_status["scopes_present"]["chat:edit"] is True
    assert twitch_status["accounts"]["bot"]["connected"] is True
    assert twitch_status["accounts"]["broadcaster"]["connected"] is True
    assert reconnect_code == 200
    assert reconnect_body["ok"] is True
    assert str(reconnect_body.get("auth_url", "")).startswith("https://id.twitch.tv/oauth2/authorize?")
    assert reconnect_body.get("redirect_uri_used") == "http://127.0.0.1/callback"
    assert connect_code == 200
    assert connect_body["ok"] is True
    assert str(connect_body.get("auth_url", "")).startswith("https://id.twitch.tv/oauth2/authorize?")
    assert connect_body.get("redirect_uri_used") == "http://127.0.0.1/callback"
    assert disconnect_code == 200
    assert disconnect_body["ok"] is True
    assert disconnect_body["status"]["accounts"]["bot"]["connected"] is False
    assert disconnect_body["status"]["accounts"]["bot"]["reason"] == "NO_TOKEN"

    provider_caps_entry = next(item for item in op_log if item["action"] == "PROVIDER_SET_CAPS")
    assert provider_caps_entry["username"] == "art"
    assert provider_caps_entry["role"] == "director"
    assert provider_caps_entry["auth_mode"] == "session"
    reconnect_entry = next(item for item in op_log if item["action"] == "TWITCH_RECONNECT")
    assert reconnect_entry["username"] == "art"
    connect_entry = next(item for item in op_log if item["action"] == "TWITCH_CONNECT_START")
    assert connect_entry["username"] == "art"
    disconnect_entry = next(item for item in op_log if item["action"] == "TWITCH_DISCONNECT")
    assert disconnect_entry["username"] == "art"


def test_twitch_status_truth_and_write_auth_requirements(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "invalid-token")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")
    monkeypatch.setenv("TWITCH_BROADCASTER_OAUTH_TOKEN", "")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, status = _request_json(base, "/api/twitch/status")
        code_connect, body_connect = _request_json(
            base,
            "/api/twitch/connect_start",
            method="POST",
            payload={"account": "bot"},
        )
        code_disconnect, body_disconnect = _request_json(
            base,
            "/api/twitch/disconnect",
            method="POST",
            payload={"account": "bot"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status["accounts"]["bot"]["connected"] is False
    assert status["accounts"]["bot"]["reason"] == "INVALID_TOKEN"
    assert status["accounts"]["broadcaster"]["connected"] is False
    assert status["accounts"]["broadcaster"]["reason"] == "NO_TOKEN"
    assert code_connect == 403
    assert body_connect["ok"] is False
    assert code_disconnect == 403
    assert body_disconnect["ok"] is False


def test_twitch_connect_start_reports_config_missing_with_auth(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TWITCH_REDIRECT_URI", raising=False)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        headers = {"Cookie": cookie}
        code, body = _request_json(
            base,
            "/api/twitch/connect_start?account=bot",
            method="POST",
            payload={},
            headers=headers,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 200
    assert body["ok"] is False
    assert body["error"] == "CONFIG_MISSING"
    missing = set(body.get("missing", []))
    assert {"TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "TWITCH_REDIRECT_URI", "PRIMARY_CHANNEL"} <= missing


def test_twitch_status_reports_missing_primary_channel_and_disables_connect(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "oauth:test-bot-token")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.delenv("TWITCH_CHANNEL", raising=False)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, status = _request_json(base, "/api/twitch/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert "PRIMARY_CHANNEL" in status.get("missing_config_fields", [])
    assert status["accounts"]["bot"]["connected"] is False
    assert status["accounts"]["bot"]["reason"] == "MISSING_PRIMARY_CHANNEL"
    assert status["accounts"]["bot"]["connect_available"] is False


def test_twitch_connect_start_uses_primary_channel_from_config_file(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")
    monkeypatch.delenv("TWITCH_CHANNEL", raising=False)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "twitch_config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "primary_channel": "ruleofrune",
                "bot_account_name": "RoonieTheCat",
                "broadcaster_account_name": "RuleOfRune",
            }
        ),
        encoding="utf-8",
    )

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        headers = {"Cookie": cookie}
        code, body = _request_json(
            base,
            "/api/twitch/connect_start?account=bot",
            method="POST",
            payload={},
            headers=headers,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 200
    assert body["ok"] is True
    assert str(body.get("auth_url", "")).startswith("https://id.twitch.tv/oauth2/authorize?")


def test_twitch_callback_completes_and_sets_connected(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")

    def _fake_exchange(self, *, code: str, redirect_uri: str, client_id: str, client_secret: str):
        assert code == "abc123"
        assert redirect_uri == "http://127.0.0.1/callback"
        assert client_id == "test-client-id"
        assert client_secret == "test-client-secret"
        return {
            "ok": True,
            "access_token": "tok_abcdefghijklmnopqrstuvwxyz123456",
            "refresh_token": "ref_abcdefghijklmnopqrstuvwxyz123456",
            "scopes": ["chat:read", "chat:edit"],
            "expires_at": "2027-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "roonie.dashboard_api.storage.DashboardStorage._exchange_twitch_code",
        _fake_exchange,
        raising=True,
    )

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        headers = {"Cookie": cookie}
        _, start = _request_json(
            base,
            "/api/twitch/connect_start?account=bot",
            method="POST",
            payload={},
            headers=headers,
        )
        state = str(start.get("state", ""))
        callback_code, callback = _request_json(
            base,
            f"/api/twitch/callback?code=abc123&state={state}",
        )
        _, status = _request_json(base, "/api/twitch/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert callback_code == 200
    assert callback["ok"] is True
    assert callback["account"] == "bot"
    assert status["accounts"]["bot"]["connected"] is True
    assert status["accounts"]["bot"]["reason"] is None


def test_twitch_callback_returns_html_for_browser_accept(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")

    def _fake_exchange(self, *, code: str, redirect_uri: str, client_id: str, client_secret: str):
        assert code == "abc123"
        assert redirect_uri == "http://127.0.0.1/callback"
        assert client_id == "test-client-id"
        assert client_secret == "test-client-secret"
        return {
            "ok": True,
            "access_token": "tok_abcdefghijklmnopqrstuvwxyz123456",
            "refresh_token": "ref_abcdefghijklmnopqrstuvwxyz123456",
            "scopes": ["chat:read", "chat:edit"],
            "expires_at": "2027-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "roonie.dashboard_api.storage.DashboardStorage._exchange_twitch_code",
        _fake_exchange,
        raising=True,
    )

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "art", "password": "art-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        headers = {"Cookie": cookie}
        _, start = _request_json(
            base,
            "/api/twitch/connect_start?account=bot",
            method="POST",
            payload={},
            headers=headers,
        )
        state = str(start.get("state", ""))
        callback_code, callback_body, callback_headers = _request_bytes_with_headers(
            base,
            f"/api/twitch/callback?code=abc123&state={state}",
            headers={"Accept": "text/html"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert callback_code == 200
    assert "text/html" in str(callback_headers.get("Content-Type", "")).lower()
    html = callback_body.decode("utf-8")
    assert "Connected." in html
    assert "Returning to dashboard" in html


def test_live_twitch_credentials_reads_local_bot_token(tmp_path: Path, monkeypatch) -> None:
    from roonie.dashboard_api.storage import DashboardStorage

    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.delenv("ROONIE_TWITCH_VALIDATE_REMOTE", raising=False)

    storage = DashboardStorage(runs_dir=runs_dir)
    auth_state_path = tmp_path / "data" / "twitch_auth_state.json"
    auth_state = {
        "version": 1,
        "accounts": {
            "bot": {
                "token": "abcdefghijklmnopqrstuvwxyz123456",
                "refresh_token": None,
                "expires_at": None,
                "scopes": ["chat:read", "chat:edit"],
                "display_name": "RoonieTheCat",
                "pending_state": None,
                "updated_at": "2026-02-14T00:00:00+00:00",
                "disconnected": False,
            },
            "broadcaster": {
                "token": None,
                "refresh_token": None,
                "expires_at": None,
                "scopes": [],
                "display_name": "RuleOfRune",
                "pending_state": None,
                "updated_at": None,
                "disconnected": False,
            },
        },
    }
    auth_state_path.write_text(json.dumps(auth_state), encoding="utf-8")

    creds = storage.get_live_twitch_credentials("bot")
    assert creds["ok"] is True
    assert creds["account"] == "bot"
    assert creds["channel"] == "ruleofrune"
    assert creds["nick"] == "RoonieTheCat"
    assert str(creds["oauth_token"]).startswith("oauth:")


def test_live_twitch_credentials_require_primary_channel(tmp_path: Path, monkeypatch) -> None:
    from roonie.dashboard_api.storage import DashboardStorage

    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1/callback")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.delenv("TWITCH_CHANNEL", raising=False)

    storage = DashboardStorage(runs_dir=runs_dir)
    auth_state_path = tmp_path / "data" / "twitch_auth_state.json"
    auth_state = {
        "version": 1,
        "accounts": {
            "bot": {
                "token": "abcdefghijklmnopqrstuvwxyz123456",
                "refresh_token": None,
                "expires_at": None,
                "scopes": ["chat:read", "chat:edit"],
                "display_name": "RoonieTheCat",
                "pending_state": None,
                "updated_at": "2026-02-14T00:00:00+00:00",
                "disconnected": False,
            },
            "broadcaster": {
                "token": None,
                "refresh_token": None,
                "expires_at": None,
                "scopes": [],
                "display_name": "RuleOfRune",
                "pending_state": None,
                "updated_at": None,
                "disconnected": False,
            },
        },
    }
    auth_state_path.write_text(json.dumps(auth_state), encoding="utf-8")

    creds = storage.get_live_twitch_credentials("bot")
    assert creds["ok"] is False
    assert creds["error"] == "MISSING_PRIMARY_CHANNEL"


def test_memory_db_initializes_tables(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    db_path = tmp_path / "data" / "memory.sqlite"
    assert not db_path.exists()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _ = _get_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert db_path.exists()
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('cultural_notes','viewer_notes','memory_audit','memory_pending')"
        ).fetchall()
    assert sorted([row[0] for row in rows]) == ["cultural_notes", "memory_audit", "memory_pending", "viewer_notes"]


def test_memory_endpoints_require_auth(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code_get, body_get = _request_json(base, "/api/memory/cultural?limit=10&offset=0")
        code_get_pending, body_get_pending = _request_json(base, "/api/memory/pending?limit=10&offset=0")
        code_post, body_post = _request_json(
            base,
            "/api/memory/cultural",
            method="POST",
            payload={"note": "Room energy is dry.", "tags": ["energy"]},
        )
        code_review, body_review = _request_json(
            base,
            "/api/memory/pending/candidate-id/approve",
            method="POST",
            payload={},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_get == 403
    assert body_get["ok"] is False
    assert code_get_pending == 403
    assert body_get_pending["ok"] is False
    assert code_post == 403
    assert body_post["ok"] is False
    assert code_review == 403
    assert body_review["ok"] is False


def test_memory_pending_ingest_approve_deny_and_learning(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    db_path = tmp_path / "data" / "memory.sqlite"

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        storage = getattr(server, "_roonie_storage")

        ingest_one = storage.ingest_memory_candidates_from_run(
            _sample_memory_intent_run(session_id="mem-s1", event_id="evt-m1", memory_object="progressive house")
        )
        assert ingest_one["seen"] == 1
        assert ingest_one["inserted"] == 1

        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        h = {"Cookie": cookie}

        code_pending, body_pending = _request_json(base, "/api/memory/pending?limit=10&offset=0", headers=h)
        assert code_pending == 200
        assert body_pending["total_count"] >= 1
        candidate_id = body_pending["items"][0]["id"]

        code_approve, body_approve = _request_json(
            base,
            f"/api/memory/pending/{candidate_id}/approve",
            method="POST",
            payload={},
            headers=h,
        )
        assert code_approve == 200
        assert body_approve["ok"] is True

        code_viewers, body_viewers = _request_json(
            base,
            "/api/memory/viewers?viewer_handle=ruleofrune&limit=20&offset=0&active_only=1",
            headers=h,
        )
        assert code_viewers == 200
        assert any("progressive house" in str(item.get("note", "")).lower() for item in body_viewers["items"])

        ingest_after_approve = storage.ingest_memory_candidates_from_run(
            _sample_memory_intent_run(session_id="mem-s2", event_id="evt-m2", memory_object="progressive house")
        )
        assert ingest_after_approve["inserted"] == 0
        assert ingest_after_approve["skipped_learned"] == 1

        ingest_two = storage.ingest_memory_candidates_from_run(
            _sample_memory_intent_run(session_id="mem-s3", event_id="evt-m3", memory_object="hard techno")
        )
        assert ingest_two["inserted"] == 1
        code_pending2, body_pending2 = _request_json(base, "/api/memory/pending?limit=10&offset=0", headers=h)
        assert code_pending2 == 200
        deny_id = body_pending2["items"][0]["id"]

        code_deny, body_deny = _request_json(
            base,
            f"/api/memory/pending/{deny_id}/deny",
            method="POST",
            payload={"reason": "too generic"},
            headers=h,
        )
        assert code_deny == 200
        assert body_deny["ok"] is True

        ingest_after_deny = storage.ingest_memory_candidates_from_run(
            _sample_memory_intent_run(session_id="mem-s4", event_id="evt-m4", memory_object="hard techno")
        )
        assert ingest_after_deny["inserted"] == 0
        assert ingest_after_deny["skipped_learned"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    audit_rows = _read_memory_audit_rows(db_path)
    pending_rows = [row for row in audit_rows if row["table_name"] == "memory_pending"]
    assert any(row["action"] == "CREATE" and row["username"] == "system" for row in pending_rows)
    assert any(row["action"] == "UPDATE" and row["username"] == "jen" for row in pending_rows)


def test_memory_crud_with_operator_session_and_audit(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    db_path = tmp_path / "data" / "memory.sqlite"

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        h = {"Cookie": cookie}

        code_create_c, body_create_c = _request_json(
            base,
            "/api/memory/cultural",
            method="POST",
            payload={"note": "Chat tends to be dry and technical.", "tags": ["tone", "chat"]},
            headers=h,
        )
        cultural_id = body_create_c.get("item", {}).get("id")
        code_list_c, list_c = _request_json(base, "/api/memory/cultural?limit=10&offset=0&active_only=1", headers=h)
        code_patch_c, body_patch_c = _request_json(
            base,
            f"/api/memory/cultural/{cultural_id}",
            method="PATCH",
            payload={"note": "Chat tends to be dry and technical. Keep responses concise.", "is_active": True},
            headers=h,
        )

        code_create_v, body_create_v = _request_json(
            base,
            "/api/memory/viewer",
            method="POST",
            payload={"viewer_handle": "@RuleOfRune", "note": "Asked about construction jobs in Seattle.", "tags": ["jobs"]},
            headers=h,
        )
        viewer_item = body_create_v.get("item", {})
        viewer_id = viewer_item.get("id")
        code_patch_v, body_patch_v = _request_json(
            base,
            f"/api/memory/viewer/{viewer_id}",
            method="PATCH",
            payload={"note": "Asked about construction jobs in Seattle and follow-up details."},
            headers=h,
        )
        code_list_v, list_v = _request_json(
            base,
            "/api/memory/viewers?viewer_handle=ruleofrune&limit=10&offset=0&active_only=1",
            headers=h,
        )
        code_del_c, body_del_c = _request_json(base, f"/api/memory/cultural/{cultural_id}", method="DELETE", headers=h)
        code_del_v, body_del_v = _request_json(base, f"/api/memory/viewer/{viewer_id}", method="DELETE", headers=h)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_create_c == 200
    assert body_create_c["ok"] is True
    assert code_list_c == 200
    assert any(item["id"] == cultural_id for item in list_c["items"])
    assert code_patch_c == 200
    assert "concise" in str(body_patch_c.get("item", {}).get("note", ""))

    assert code_create_v == 200
    assert body_create_v["ok"] is True
    assert viewer_item["viewer_handle"] == "ruleofrune"
    assert code_patch_v == 200
    assert code_list_v == 200
    assert any(item["id"] == viewer_id for item in list_v["items"])
    assert code_del_c == 200
    assert code_del_v == 200
    assert body_del_c["ok"] is True
    assert body_del_v["ok"] is True
    assert _memory_row_exists(db_path, "cultural_notes", cultural_id) is False
    assert _memory_row_exists(db_path, "viewer_notes", viewer_id) is False

    audit_rows = _read_memory_audit_rows(db_path)
    assert len(audit_rows) >= 6
    d7_rows = [row for row in audit_rows if row["table_name"] in {"cultural_notes", "viewer_notes"}]
    assert any(row["action"] == "CREATE" and row["table_name"] == "cultural_notes" for row in d7_rows)
    assert any(row["action"] == "UPDATE" and row["table_name"] == "cultural_notes" for row in d7_rows)
    assert any(row["action"] == "DELETE" and row["table_name"] == "cultural_notes" for row in d7_rows)
    assert any(row["action"] == "CREATE" and row["table_name"] == "viewer_notes" for row in d7_rows)
    assert any(row["action"] == "UPDATE" and row["table_name"] == "viewer_notes" for row in d7_rows)
    assert any(row["action"] == "DELETE" and row["table_name"] == "viewer_notes" for row in d7_rows)
    delete_c = next(row for row in d7_rows if row["action"] == "DELETE" and row["table_name"] == "cultural_notes")
    delete_v = next(row for row in d7_rows if row["action"] == "DELETE" and row["table_name"] == "viewer_notes")
    assert delete_c["row_id"] == cultural_id
    assert delete_v["row_id"] == viewer_id
    assert delete_c["before_hash"]
    assert delete_v["before_hash"]
    assert delete_c["after_hash"] is None
    assert delete_v["after_hash"] is None
    for row in d7_rows:
        assert row["username"] == "jen"
        assert row["role"] == "operator"
        assert row["auth_mode"] == "session"


def test_memory_validation_rejects_inference_and_bounds(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        h = {"Cookie": cookie}

        code_empty, _ = _request_json(
            base,
            "/api/memory/cultural",
            method="POST",
            payload={"note": "   ", "tags": []},
            headers=h,
        )
        code_long, _ = _request_json(
            base,
            "/api/memory/cultural",
            method="POST",
            payload={"note": "x" * 501, "tags": []},
            headers=h,
        )
        code_infer, body_infer = _request_json(
            base,
            "/api/memory/cultural",
            method="POST",
            payload={"note": "They seem like they are probably new here.", "tags": []},
            headers=h,
        )
        code_identity, body_identity = _request_json(
            base,
            "/api/memory/viewer",
            method="POST",
            payload={"viewer_handle": "ruleofrune", "note": "Likely a religion-based preference.", "tags": []},
            headers=h,
        )
        code_explicit, body_explicit = _request_json(
            base,
            "/api/memory/cultural",
            method="POST",
            payload={"note": "Viewer explicitly asked for track IDs in chat.", "tags": []},
            headers=h,
        )
        code_neutral, body_neutral = _request_json(
            base,
            "/api/memory/viewer",
            method="POST",
            payload={"viewer_handle": "ruleofrune", "note": "Viewer said they prefer short replies.", "tags": []},
            headers=h,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_empty == 400
    assert code_long == 400
    assert code_infer == 400
    assert body_infer.get("detail") == "Memory must be explicit and non-inferential."
    assert code_identity == 400
    assert body_identity.get("detail") == "Memory must be explicit and non-inferential."
    assert code_explicit == 200
    assert body_explicit["ok"] is True
    assert code_neutral == 200
    assert body_neutral["ok"] is True


def test_memory_viewer_handle_normalization_is_consistent(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ROONIE_OPERATOR_KEY", raising=False)
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, _, login_headers = _request_json_with_headers(
            base,
            "/api/auth/login",
            method="POST",
            payload={"username": "jen", "password": "jen-pass-123"},
        )
        cookie = _cookie_from_response_headers(login_headers)
        h = {"Cookie": cookie}
        code_create, created = _request_json(
            base,
            "/api/memory/viewer",
            method="POST",
            payload={"viewer_handle": "RuleOfRune", "note": "Asked for a set replay timestamp.", "tags": []},
            headers=h,
        )
        code_query, queried = _request_json(
            base,
            "/api/memory/viewers?viewer_handle=ruleofrune&limit=10&offset=0&active_only=1",
            headers=h,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_create == 200
    assert created["ok"] is True
    row = created["item"]
    assert row["viewer_handle"] == "ruleofrune"
    assert code_query == 200
    items = queried["items"]
    assert any(item["id"] == row["id"] for item in items)
    for item in items:
        assert item["viewer_handle"] == item["viewer_handle"].strip().lower()


def test_memory_legacy_key_auth_mode_audit(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    db_path = tmp_path / "data" / "memory.sqlite"

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_json(
            base,
            "/api/memory/viewer",
            method="POST",
            payload={"viewer_handle": "ruleofrune", "note": "Explicitly asked for job help.", "tags": ["jobs"]},
            headers=headers,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 200
    assert body["ok"] is True
    audit_rows = _read_memory_audit_rows(db_path)
    assert len(audit_rows) >= 1
    latest = audit_rows[0]
    assert latest["action"] == "CREATE"
    assert latest["table_name"] == "viewer_notes"
    assert latest["username"] == "jen"
    assert latest["role"] == "operator"
    assert latest["auth_mode"] == "legacy_key"


def test_senses_status_off_with_guardrails(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)
    senses_path = tmp_path / "data" / "senses_config.json"
    assert not senses_path.exists()

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        body = _get_json(base, "/api/senses/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert senses_path.exists()
    assert body["enabled"] is False
    assert body["local_only"] is True
    assert body["whitelist"] == ["Art", "Jen"]
    assert body["purpose"] == "avoid_interrupting_hosts"
    assert body["never_initiate"] is True
    assert body["never_publicly_reference_detection"] is True
    assert body["no_viewer_recognition"] is True
    assert body["live_hard_disabled"] is True
    assert body["reason"] == "Senses disabled by Canon; not active in Live."


def test_senses_enable_attempt_is_forbidden(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _write_sample_run(runs_dir)
    _set_dashboard_paths(monkeypatch, tmp_path)

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code, body = _request_json(base, "/api/senses/enable", method="POST", payload={"enabled": True})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code == 403
    assert body["ok"] is False
    assert body["detail"] == "Senses are disabled by Canon in this build."


def test_senses_allowed_guard_always_false() -> None:
    from roonie.live_director import senses_allowed

    assert senses_allowed({}) is False
    assert senses_allowed({"mode": "live"}) is False
