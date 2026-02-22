from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from live_shim.record_run import run_payload
from roonie.dashboard_api.app import create_server
from roonie.types import DecisionRecord, Env, Event


def _set_runtime_paths(monkeypatch, tmp_path: Path) -> Path:
    runs_dir = tmp_path / "runs"
    monkeypatch.setenv("ROONIE_DASHBOARD_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")
    _SESSION_COOKIE_CACHE.clear()
    return runs_dir


def _provider_stub(text: str, approved_emotes: List[str]):
    def _stub(self, event: Event, env: Env) -> DecisionRecord:
        _ = env
        session_id = str(event.metadata.get("session_id", "")).strip() or None
        return DecisionRecord(
            case_id="live",
            event_id=event.event_id,
            action="RESPOND_PUBLIC",
            route="primary:openai",
            response_text=text,
            trace={
                "director": {"type": "ProviderDirector"},
                "behavior": {
                    "category": "BANTER",
                    "approved_emotes": list(approved_emotes),
                },
                "proposal": {
                    "text": text,
                    "message_text": event.message,
                    "provider_used": "openai",
                    "route_used": "primary:openai",
                    "moderation_status": "allow",
                    "session_id": session_id,
                    "token_usage_if_available": None,
                },
            },
            context_active=False,
            context_turns_used=0,
        )

    return _stub


def _live_payload(session_id: str, event_id: str, message: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "active_director": "ProviderDirector",
        "inputs": [
            {
                "event_id": event_id,
                "message": message,
                "metadata": {
                    "user": "ruleofrune",
                    "is_direct_mention": True,
                    "mode": "live",
                    "platform": "twitch",
                },
            }
        ],
    }


def _start_server(runs_dir: Path):
    server = create_server(host="127.0.0.1", port=0, runs_dir=runs_dir)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return server, thread


def _get_json(base: str, path: str):
    headers = _with_auto_cookie(base, path)
    request = urllib.request.Request(
        f"{base}{path}",
        method="GET",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


_AUTO_AUTH_GET_PATHS = {
    "/api/suppressions",
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


def _with_auto_cookie(base: str, path: str) -> Dict[str, str]:
    if _path_only(path) not in _AUTO_AUTH_GET_PATHS:
        return {}
    cookie = _login_cookie(base)
    return {"Cookie": cookie} if cookie else {}


def test_approved_emote_allow_list_enforced_and_logged(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    runs_dir = _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    send_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        send_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    allow_list = ["PogChamp", "Kappa"]

    # a) only allowed emotes -> allowed through OutputGate while armed.
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub("hello PogChamp Kappa", allow_list),
    )
    allowed_path = run_payload(
        _live_payload("allow-emotes-session", "evt-allow", "@RoonieTheCat hey there"),
        emit_outputs=True,
    )
    allowed_doc = json.loads(allowed_path.read_text(encoding="utf-8"))
    assert allowed_doc["outputs"][0]["emitted"] is True
    assert allowed_doc["outputs"][0]["reason"] == "EMITTED"
    assert len(send_calls) == 1

    # b) includes disallowed emote -> suppressed with DISALLOWED_EMOTE, no send.
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub("hello BibleThump", allow_list),
    )
    blocked_path = run_payload(
        _live_payload("block-emote-session", "evt-block", "@RoonieTheCat hey again"),
        emit_outputs=True,
    )
    blocked_doc = json.loads(blocked_path.read_text(encoding="utf-8"))
    assert blocked_doc["outputs"][0]["emitted"] is False
    assert blocked_doc["outputs"][0]["reason"] == "DISALLOWED_EMOTE"
    assert len(send_calls) == 1

    server, thread = _start_server(runs_dir)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        suppressions = _get_json(base, "/api/suppressions?limit=20")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert isinstance(suppressions, list)
    assert any(item.get("suppression_reason") == "DISALLOWED_EMOTE" for item in suppressions if isinstance(item, dict))


def test_ruleof6_emote_with_description_format_is_enforced(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _ = _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    send_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        send_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    # Simulates approved emotes passed through metadata in "name (desc)" form.
    allow_list = ["ruleof6Cheshire (cheshire grin)", "ruleof6Party (party)"]

    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub("hello ruleof6Cheshire", allow_list),
    )
    allowed_path = run_payload(
        _live_payload("ruleof6-allow", "evt-ruleof6-allow", "@RoonieTheCat hey there"),
        emit_outputs=True,
    )
    allowed_doc = json.loads(allowed_path.read_text(encoding="utf-8"))
    assert allowed_doc["outputs"][0]["emitted"] is True
    assert allowed_doc["outputs"][0]["reason"] == "EMITTED"
    assert len(send_calls) == 1

    # Token includes digit->uppercase transition ("6N"), which old logic missed.
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub("hello ruleof6Nope", allow_list),
    )
    blocked_path = run_payload(
        _live_payload("ruleof6-block", "evt-ruleof6-block", "@RoonieTheCat hey again"),
        emit_outputs=True,
    )
    blocked_doc = json.loads(blocked_path.read_text(encoding="utf-8"))
    assert blocked_doc["outputs"][0]["emitted"] is False
    assert blocked_doc["outputs"][0]["reason"] == "DISALLOWED_EMOTE"
    assert len(send_calls) == 1


def test_mention_username_with_digit_does_not_trigger_disallowed_emote(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _ = _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    send_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        send_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    allow_list = ["ruleof6Hey (hey greeting)"]
    response_text = "@cland3stine yeah Art, I'm here. ruleof6Hey"
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub(response_text, allow_list),
    )

    allowed_path = run_payload(
        _live_payload("allow-mention-digit", "evt-mention-digit", "@RoonieTheCat Roonie?"),
        emit_outputs=True,
    )
    allowed_doc = json.loads(allowed_path.read_text(encoding="utf-8"))
    assert allowed_doc["outputs"][0]["emitted"] is True
    assert allowed_doc["outputs"][0]["reason"] == "EMITTED"
    assert len(send_calls) == 1


def test_pascal_case_proper_noun_mid_sentence_does_not_trigger_disallowed_emote(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _ = _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    send_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        send_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    allow_list = ["ruleof6Coolcat (cool cat)"]
    response_text = (
        "@c0rcyra yeah, I'm basically housebroken to this booth at this point. "
        "If there's a RuleOfRune night on, odds are I'm perched somewhere judging the blend points. "
        "ruleof6Coolcat"
    )
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub(response_text, allow_list),
    )

    allowed_path = run_payload(
        _live_payload("allow-proper-noun", "evt-proper-noun", "you come here often, @RoonieTheCat ?"),
        emit_outputs=True,
    )
    allowed_doc = json.loads(allowed_path.read_text(encoding="utf-8"))
    assert allowed_doc["outputs"][0]["emitted"] is True
    assert allowed_doc["outputs"][0]["reason"] == "EMITTED"
    assert len(send_calls) == 1


def test_echoed_disallowed_emote_token_from_viewer_message_is_allowed(tmp_path, monkeypatch) -> None:
    import responders.output_gate as output_gate

    _ = _set_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROONIE_OUTPUT_DISABLED", "0")
    monkeypatch.setenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "0")
    output_gate._LAST_EMIT_TS = 0.0
    output_gate._LAST_EMIT_BY_KEY.clear()

    send_calls: List[Dict[str, Any]] = []

    def _spy_handle_output(self, output: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        send_calls.append({"output": dict(output), "metadata": dict(metadata)})

    monkeypatch.setattr("adapters.twitch_output.TwitchOutputAdapter.handle_output", _spy_handle_output)

    allow_list = ["ruleof6Coolcat (cool cat)"]
    response_text = (
        "@c0rcyra infilt6JOCO2 always reads to me like "
        "\"JOCO has entered the chat\". ruleof6Coolcat"
    )
    monkeypatch.setattr(
        "roonie.provider_director.ProviderDirector.evaluate",
        _provider_stub(response_text, allow_list),
    )

    allowed_path = run_payload(
        _live_payload(
            "allow-echoed-disallowed-token",
            "evt-echoed-disallowed-token",
            "how about this one -> infilt6JOCO2 @RoonieTheCat",
        ),
        emit_outputs=True,
    )
    allowed_doc = json.loads(allowed_path.read_text(encoding="utf-8"))
    assert allowed_doc["outputs"][0]["emitted"] is True
    assert allowed_doc["outputs"][0]["reason"] == "EMITTED"
    assert len(send_calls) == 1
