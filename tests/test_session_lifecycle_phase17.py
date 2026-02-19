from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from live_shim.record_run import run_payload
from roonie.dashboard_api.app import create_server


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    _SESSION_COOKIE_CACHE.clear()


_AUTO_AUTH_GET_PATHS = {
    "/api/status",
    "/api/operator_log",
    "/api/twitch/status",
}

_SESSION_COOKIE_CACHE: Dict[str, str] = {}


def _path_only(path: str) -> str:
    return str(urlparse(str(path or "")).path or "")


def _login_cookie(base: str) -> str:
    cached = _SESSION_COOKIE_CACHE.get(base)
    if cached:
        return cached
    payload = json.dumps({"username": "jen", "password": "jen-pass-123"}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/auth/login",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            raw = str(response.headers.get("Set-Cookie", "")).strip()
    except urllib.error.HTTPError:
        return ""
    cookie = raw.split(";", 1)[0].strip() if raw else ""
    if cookie:
        _SESSION_COOKIE_CACHE[base] = cookie
    return cookie


def _with_auto_cookie(base: str, path: str, method: str, headers: Dict[str, str] | None) -> Dict[str, str]:
    req_headers = dict(headers or {})
    if str(method or "GET").upper() != "GET":
        return req_headers
    if "Cookie" in req_headers or "X-ROONIE-OP-KEY" in req_headers:
        return req_headers
    if _path_only(path) not in _AUTO_AUTH_GET_PATHS:
        return req_headers
    cookie = _login_cookie(base)
    if cookie:
        req_headers["Cookie"] = cookie
    return req_headers


def _request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
) -> tuple[int, Dict[str, Any] | List[Dict[str, Any]]]:
    body = None
    req_headers = _with_auto_cookie(base, path, method, headers)
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


def _start_server(runs_dir: Path):
    server = create_server(host="127.0.0.1", port=0, runs_dir=runs_dir)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return server, thread


def _write_provider_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": 500,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {"day": "2026-02-15", "requests": 0, "tokens": 0},
            }
        ),
        encoding="utf-8",
    )


def _write_routing_config(path: Path, *, enabled: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": enabled,
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


def test_fresh_launch_forces_disarmed_and_ignores_persisted_arm_state(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _set_dashboard_paths(monkeypatch, tmp_path)
    control_path = tmp_path / "data" / "control_state.json"
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text(
        json.dumps(
            {
                "armed": True,
                "output_disabled": False,
                "silence_until": None,
                "active_director": "ProviderDirector",
            }
        ),
        encoding="utf-8",
    )

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, status = _request_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status["armed"] is False
    assert status["can_post"] is False
    assert "DISARMED" in status["blocked_by"]


def test_arm_generates_new_session_each_call_and_audits(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        code_a1, arm_1 = _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        code_a2, arm_2 = _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _, status = _request_json(base, "/api/status")
        _, op_log = _request_json(base, "/api/operator_log?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_a1 == 200
    assert code_a2 == 200
    sid1 = str(arm_1["state"].get("session_id", "")).strip()
    sid2 = str(arm_2["state"].get("session_id", "")).strip()
    assert sid1
    assert sid2
    assert sid1 != sid2
    assert status["armed"] is True
    assert status["session_id"] == sid2

    arm_actions = [item for item in op_log if item.get("action") == "CONTROL_ARM_SET"]
    assert len(arm_actions) >= 2
    latest_payload = json.loads(str(arm_actions[0].get("payload_summary") or "{}"))
    assert latest_payload.get("new_armed") is True
    assert str(latest_payload.get("session_id", "")).strip()


def test_disarm_suppresses_output_immediately_even_with_provider_proposal(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    _set_dashboard_paths(monkeypatch, tmp_path)
    _write_provider_config(providers_path)
    _write_routing_config(routing_path, enabled=True)
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _request_json(base, "/api/live/disarm", method="POST", payload={}, headers=headers)
        _, status = _request_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    calls: list[dict] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)
    out_path = run_payload(
        {
            "session_id": str(status.get("session_id") or "phase17-disarmed"),
            "active_director": "ProviderDirector",
            "inputs": [
                {
                    "event_id": "evt-1",
                    "message": "@RoonieTheCat how are you?",
                    "metadata": {
                        "user": "ruleofrune",
                        "is_direct_mention": True,
                        "mode": "live",
                        "platform": "twitch",
                    },
                }
            ],
        },
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert run_doc["outputs"][0]["emitted"] is False
    assert run_doc["outputs"][0]["reason"] == "OUTPUT_DISABLED"
    assert str(run_doc["outputs"][0].get("session_id", "")).strip() == str(
        status.get("session_id") or "phase17-disarmed"
    ).strip()
    assert str(
        run_doc["decisions"][0].get("trace", {}).get("proposal", {}).get("session_id", "")
    ).strip() == str(status.get("session_id") or "phase17-disarmed").strip()
    assert calls == []


def test_twitch_connection_status_independent_of_armed_state(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _set_dashboard_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TWITCH_REDIRECT_URI", "http://127.0.0.1:8787/api/twitch/callback")
    monkeypatch.setenv("PRIMARY_CHANNEL", "ruleofrune")
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")
    monkeypatch.setenv("TWITCH_NICK", "RoonieTheCat")
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "oauth:abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("ROONIE_TWITCH_VALIDATE_REMOTE", "0")
    monkeypatch.setenv("ROONIE_KILL_SWITCH", "0")

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, status_before = _request_json(base, "/api/status")
        _request_json(base, "/api/live/arm", method="POST", payload={}, headers=headers)
        _, status_armed = _request_json(base, "/api/status")
        _request_json(base, "/api/live/disarm", method="POST", payload={}, headers=headers)
        _, status_disarmed = _request_json(base, "/api/status")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert status_before["twitch_connected"] is True
    assert status_armed["twitch_connected"] is True
    assert status_disarmed["twitch_connected"] is True
