from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
from urllib.parse import urlparse

from live_shim.record_run import run_payload
from roonie.dashboard_api.app import create_server


def _today_ny() -> str:
    try:
        from providers.router import _today_ny as _provider_today_ny

        return _provider_today_ny()
    except Exception:
        return datetime.utcnow().date().isoformat()


def _set_dashboard_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    _SESSION_COOKIE_CACHE.clear()


def _write_provider_config(path: Path, *, active_provider: str = "openai", requests_max: int = 500, requests_used: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": active_provider,
                "approved_providers": ["openai", "grok"],
                "caps": {
                    "daily_requests_max": requests_max,
                    "daily_tokens_max": 0,
                    "hard_stop_on_cap": True,
                },
                "usage": {
                    "day": _today_ny(),
                    "requests": requests_used,
                    "tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_routing_config(path: Path, *, enabled: bool, manual_override: str = "default") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": enabled,
                "default_provider": "openai",
                "music_route_provider": "grok",
                "moderation_provider": "openai",
                "manual_override": manual_override,
                "classification_rules": {
                    "music_culture_keywords": [
                        "track",
                        "id",
                        "rekordbox",
                        "bpm",
                        "key",
                        "producer",
                        "dj",
                        "label",
                        "remix",
                        "mix",
                        "release",
                        "set",
                        "tune",
                    ],
                    "artist_title_pattern": True,
                },
            }
        ),
        encoding="utf-8",
    )


def _live_payload(
    *,
    session_id: str,
    message: str,
    active_director: str,
    event_id: str = "evt-1",
    extra_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    metadata = {
        "user": "ruleofrune",
        "is_direct_mention": True,
        "mode": "live",
        "platform": "twitch",
    }
    if isinstance(extra_metadata, dict):
        metadata.update(extra_metadata)
    return {
        "session_id": session_id,
        "active_director": active_director,
        "inputs": [
            {
                "event_id": event_id,
                "message": message,
                "metadata": metadata,
            }
        ],
    }


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


_AUTO_AUTH_GET_PATHS = {
    "/api/status",
    "/api/operator_log",
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


def _write_sample_run(runs_dir: Path, *, session_id: str) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    sample = {
        "schema_version": "run-v1",
        "session_id": session_id,
        "director_commit": "abc123",
        "started_at": "2026-02-15T20:00:00+00:00",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hello",
                "actor": "viewer",
                "metadata": {"user": "ruleofrune", "mode": "live", "is_direct_mention": True},
            }
        ],
        "decisions": [
            {
                "case_id": "live",
                "event_id": "evt-1",
                "action": "RESPOND_PUBLIC",
                "route": "primary:openai",
                "response_text": "ok",
                "trace": {},
                "context_active": False,
                "context_turns_used": 0,
            }
        ],
        "outputs": [
            {"event_id": "evt-1", "emitted": True, "reason": "EMITTED", "sink": "stdout"},
        ],
    }
    (runs_dir / "run.json").write_text(json.dumps(sample), encoding="utf-8")


def test_toggle_combo_provider_director_routing_on_routes_music_only_to_grok(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    _write_provider_config(providers_path, active_provider="openai")
    _write_routing_config(routing_path, enabled=True)

    general_path = run_payload(
        _live_payload(
            session_id="routing-on-general",
            message="@RoonieTheCat can you help with this?",
            active_director="ProviderDirector",
        ),
        emit_outputs=False,
    )
    general_doc = json.loads(general_path.read_text(encoding="utf-8"))
    general_decision = general_doc["decisions"][0]
    assert general_doc["active_director"] == "ProviderDirector"
    assert general_decision["trace"]["proposal"]["provider_used"] == "openai"
    assert str(general_decision["response_text"]).startswith("[openai stub]")

    music_path = run_payload(
        _live_payload(
            session_id="routing-on-music",
            message="@RoonieTheCat what track is this?",
            active_director="ProviderDirector",
        ),
        emit_outputs=False,
    )
    music_doc = json.loads(music_path.read_text(encoding="utf-8"))
    music_decision = music_doc["decisions"][0]
    assert music_doc["active_director"] == "ProviderDirector"
    provider_used = str(music_decision["trace"]["proposal"]["provider_used"])
    if provider_used == "none":
        assert music_decision["route"] == "behavior:track_id"
        assert "track" in str(music_decision["response_text"]).lower()
    else:
        assert provider_used == "grok"
        moderation_status = music_decision["trace"]["proposal"]["moderation_status"]
        assert moderation_status in {"allow", "block"}
        if moderation_status == "allow":
            assert str(music_decision["response_text"]).startswith("[grok stub]")
        else:
            assert music_decision["response_text"] is None


def test_toggle_combo_provider_director_routing_off_never_calls_grok(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    _write_provider_config(providers_path, active_provider="grok")
    _write_routing_config(routing_path, enabled=False, manual_override="force_grok")

    out_path = run_payload(
        _live_payload(
            session_id="routing-off-music",
            message="@RoonieTheCat what track is this?",
            active_director="ProviderDirector",
        ),
        emit_outputs=False,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    assert run_doc["active_director"] == "ProviderDirector"
    provider_used = str(decision["trace"]["proposal"]["provider_used"])
    assert provider_used in {"openai", "none"}
    if provider_used == "openai":
        assert str(decision["response_text"]).startswith("[openai stub]")
    else:
        assert decision["route"] == "behavior:track_id"


def test_toggle_combo_offline_director_ignores_routing_state_and_provider_path(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    _write_provider_config(providers_path, active_provider="openai")

    for enabled in (True, False):
        _write_routing_config(routing_path, enabled=enabled)
        out_path = run_payload(
            _live_payload(
                session_id=f"offline-routing-{int(enabled)}",
                message="@RoonieTheCat what track is this?",
                active_director="OfflineDirector",
            ),
            emit_outputs=False,
        )
        run_doc = json.loads(out_path.read_text(encoding="utf-8"))
        decision = run_doc["decisions"][0]
        assert run_doc["active_director"] == "OfflineDirector"
        assert decision.get("route", "").startswith("responder:")
        assert not str(decision.get("response_text", "")).startswith("[grok stub]")
        assert not str(decision.get("response_text", "")).startswith("[openai stub]")
        trace = decision.get("trace", {})
        assert trace.get("director", {}).get("type") != "ProviderDirector"


def test_output_gate_disarmed_never_calls_twitch_adapter(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "1")
    _write_provider_config(providers_path, active_provider="openai")
    _write_routing_config(routing_path, enabled=True)

    calls: list[dict] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    out_path = run_payload(
        _live_payload(
            session_id="disarmed-gate",
            message="@RoonieTheCat what track is this?",
            active_director="ProviderDirector",
        ),
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert run_doc["outputs"][0]["emitted"] is False
    assert run_doc["outputs"][0]["reason"] == "OUTPUT_DISABLED"
    assert calls == []


def test_output_gate_armed_rate_limit_and_cost_cap_still_gate_posts(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "999")
    output_gate._LAST_EMIT_TS = 0.0
    _write_provider_config(providers_path, active_provider="openai")
    _write_routing_config(routing_path, enabled=True)

    calls: list[dict] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    payload = {
        "session_id": "armed-rate-limit",
        "active_director": "ProviderDirector",
        "inputs": [
            {
                "event_id": "evt-1",
                "message": "@RoonieTheCat hey there?",
                "metadata": {"user": "ruleofrune", "is_direct_mention": True, "mode": "live", "platform": "twitch"},
            },
                {
                    "event_id": "evt-2",
                    "message": "@RoonieTheCat how are things?",
                    "metadata": {"user": "ruleofrune", "is_direct_mention": True, "mode": "live", "platform": "twitch"},
                },
            ],
        }
    run_path = run_payload(payload, emit_outputs=True)
    run_doc = json.loads(run_path.read_text(encoding="utf-8"))
    assert run_doc["outputs"][0]["emitted"] is True
    assert run_doc["outputs"][1]["emitted"] is False
    assert run_doc["outputs"][1]["reason"] == "RATE_LIMIT"
    assert len(calls) == 1

    # Cost cap reached: no postable output even when armed.
    output_gate._LAST_EMIT_TS = 0.0
    _write_provider_config(providers_path, active_provider="openai", requests_max=1, requests_used=1)
    capped_path = run_payload(
        _live_payload(
            session_id="armed-cost-cap",
            message="@RoonieTheCat can you help?",
            active_director="ProviderDirector",
        ),
        emit_outputs=True,
    )
    capped_doc = json.loads(capped_path.read_text(encoding="utf-8"))
    assert capped_doc["decisions"][0]["action"] == "NOOP"
    assert capped_doc["outputs"][0]["emitted"] is False
    assert capped_doc["outputs"][0]["reason"] == "COST_CAP"


def test_provider_failure_no_post_and_no_auto_fallback(tmp_path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    providers_path = tmp_path / "data" / "providers_config.json"
    routing_path = tmp_path / "data" / "routing_config.json"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    _write_provider_config(providers_path, active_provider="openai")
    _write_routing_config(routing_path, enabled=True)

    calls: list[dict] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    out_path = run_payload(
        _live_payload(
            session_id="provider-error",
            message="@RoonieTheCat can you help?",
            active_director="ProviderDirector",
            extra_metadata={"provider_test_overrides": {"primary_behavior": "throw"}},
        ),
        emit_outputs=True,
    )
    run_doc = json.loads(out_path.read_text(encoding="utf-8"))
    decision = run_doc["decisions"][0]
    output = run_doc["outputs"][0]
    assert run_doc["active_director"] == "ProviderDirector"
    assert decision["action"] == "NOOP"
    assert decision["trace"]["provider_block_reason"] == "PROVIDER_ERROR"
    assert output["emitted"] is False
    assert output["reason"] == "PROVIDER_ERROR"
    assert calls == []


def test_status_endpoint_reflects_toggle_state_and_audit_correlation(tmp_path: Path, monkeypatch) -> None:
    runs_dir = tmp_path / "runs"
    _set_dashboard_paths(monkeypatch, tmp_path)
    _write_sample_run(runs_dir, session_id=str(uuid4()))
    monkeypatch.setenv("ROONIE_OPERATOR_KEY", "op-key-123")

    headers = {"X-ROONIE-OP-KEY": "op-key-123", "X-ROONIE-ACTOR": "jen"}
    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _, initial = _request_json(base, "/api/status")
        code_routing, _ = _request_json(
            base,
            "/control/routing",
            method="POST",
            payload={"enabled": False},
            headers=headers,
        )
        code_director, _ = _request_json(
            base,
            "/control/director",
            method="POST",
            payload={"active": "OfflineDirector"},
            headers=headers,
        )
        _, status = _request_json(base, "/api/status")
        _, operator_log = _request_json(base, "/api/operator_log?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert code_routing == 200
    assert code_director == 200
    assert isinstance(initial, dict)
    assert isinstance(status, dict)
    assert initial["routing_enabled"] is True
    initial_director = str(initial.get("active_director"))
    assert status["routing_enabled"] is False
    assert status["active_director"] == "OfflineDirector"
    assert isinstance(status["armed"], bool)
    assert (status.get("session_id") is None) or isinstance(status.get("session_id"), str)

    assert isinstance(operator_log, list)
    by_action = {item.get("action"): item for item in operator_log if isinstance(item, dict)}
    routing_action = by_action.get("CONTROL_ROUTING_SET")
    director_action = by_action.get("CONTROL_DIRECTOR_SET")
    assert routing_action is not None
    assert director_action is not None
    assert routing_action.get("actor") == "jen"
    assert director_action.get("actor") == "jen"
    routing_payload = json.loads(str(routing_action.get("payload_summary") or "{}"))
    director_payload = json.loads(str(director_action.get("payload_summary") or "{}"))
    assert routing_payload.get("old", {}).get("enabled") is True
    assert routing_payload.get("new", {}).get("enabled") is False
    assert director_payload.get("old") == initial_director
    assert director_payload.get("new") == "OfflineDirector"
