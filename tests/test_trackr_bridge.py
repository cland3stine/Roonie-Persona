"""Tests for TrackrBridge + storage + API endpoints + enrichment (Phases A & B)."""
from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest


# ── TrackrBridge unit tests ───────────────────────────────────


def test_parse_track_line_artist_title():
    from roonie.control_room.trackr_bridge import _parse_track_line

    result = _parse_track_line("Above & Beyond - Sun & Moon")
    assert result["artist"] == "Above & Beyond"
    assert result["title"] == "Sun & Moon"
    assert result["raw"] == "Above & Beyond - Sun & Moon"


def test_parse_track_line_no_dash():
    from roonie.control_room.trackr_bridge import _parse_track_line

    result = _parse_track_line("Just A Title")
    assert result["artist"] == ""
    assert result["title"] == "Just A Title"


def test_parse_track_line_empty():
    from roonie.control_room.trackr_bridge import _parse_track_line

    result = _parse_track_line("")
    assert result["raw"] == ""
    assert result["artist"] == ""
    assert result["title"] == ""


def test_parse_track_line_em_dash():
    from roonie.control_room.trackr_bridge import _parse_track_line

    result = _parse_track_line("Artist \u2014 Title")
    assert result["artist"] == "Artist"
    assert result["title"] == "Title"


def test_parse_track_line_en_dash():
    from roonie.control_room.trackr_bridge import _parse_track_line

    result = _parse_track_line("Artist \u2013 Title")
    assert result["artist"] == "Artist"
    assert result["title"] == "Title"


def test_bridge_push_state_sets_trackr_state():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_storage = MagicMock()
    mock_storage.get_trackr_config.return_value = {"enabled": False}
    bridge = TrackrBridge(storage=mock_storage)

    bridge._push_state(connected=True, enabled=True, last_current="Dj - Track")
    mock_storage.set_trackr_state.assert_called_once()

    state = mock_storage.set_trackr_state.call_args[0][0]
    assert state["connected"] is True
    assert state["enabled"] is True
    assert state["current"]["artist"] == "Dj"
    assert state["current"]["title"] == "Track"


def test_bridge_push_state_handles_no_storage_method():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_storage = MagicMock(spec=[])  # no set_trackr_state
    bridge = TrackrBridge(storage=mock_storage)
    # Should not raise
    bridge._push_state(connected=False)


def test_bridge_fetch_trackr_parses_json():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "current": "Artist - Title",
        "previous": "",
        "is_running": True,
        "device_count": 2,
    }).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        data = TrackrBridge._fetch_trackr("http://127.0.0.1:8755")
    assert data["current"] == "Artist - Title"
    assert data["is_running"] is True


def test_bridge_start_stop():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_storage = MagicMock()
    mock_storage.get_trackr_config.return_value = {"enabled": False}
    bridge = TrackrBridge(storage=mock_storage)

    bridge.start()
    assert bridge.is_running()

    bridge.stop()
    bridge.join(timeout=3.0)
    assert not bridge.is_running()


def test_bridge_disabled_does_not_poll():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_storage = MagicMock()
    mock_storage.get_trackr_config.return_value = {"enabled": False}
    bridge = TrackrBridge(storage=mock_storage)

    bridge.start()
    time.sleep(0.3)
    bridge.stop()
    bridge.join(timeout=3.0)

    # set_trackr_state should have been called with connected=False, enabled=False
    calls = mock_storage.set_trackr_state.call_args_list
    assert len(calls) >= 1
    last_state = calls[-1][0][0]
    assert last_state["connected"] is False


# ── Storage tests ─────────────────────────────────────────────


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_OPERATOR_KEY", "test-key-123")
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))

    from roonie.dashboard_api.storage import DashboardStorage
    return DashboardStorage(runs_dir=tmp_path / "runs")


def test_trackr_config_defaults(tmp_storage):
    config = tmp_storage.get_trackr_config()
    assert config["enabled"] is False
    assert config["api_url"] == "http://127.0.0.1:8755"
    assert config["poll_interval_seconds"] == 3.0
    assert config["track_id_skill_enabled"] is False
    assert config["proactive_favorites_enabled"] is False
    assert config["proactive_play_threshold"] == 3
    assert config["proactive_max_per_session"] == 3


def test_trackr_config_update_patch(tmp_storage):
    new, audit = tmp_storage.update_trackr_config(
        {"enabled": True, "api_url": "http://192.168.1.100:8755"},
        actor="art",
        patch=True,
    )
    assert new["enabled"] is True
    assert new["api_url"] == "http://192.168.1.100:8755"
    assert new["poll_interval_seconds"] == 3.0  # unchanged
    assert "enabled" in audit["changed_keys"]
    assert "api_url" in audit["changed_keys"]


def test_trackr_config_update_put(tmp_storage):
    new, audit = tmp_storage.update_trackr_config(
        {"enabled": True},
        actor="jen",
        patch=False,
    )
    assert new["enabled"] is True
    assert new["api_url"] == "http://127.0.0.1:8755"  # default restored


def test_trackr_config_normalize_clamping(tmp_storage):
    new, _ = tmp_storage.update_trackr_config(
        {"poll_interval_seconds": 999, "proactive_play_threshold": -5},
        actor="art",
        patch=True,
    )
    assert new["poll_interval_seconds"] == 3.0  # clamped to default
    assert new["proactive_play_threshold"] == 3  # clamped to default


def test_trackr_config_persists(tmp_storage):
    tmp_storage.update_trackr_config(
        {"enabled": True, "api_url": "http://10.0.0.5:8755"},
        actor="art",
        patch=True,
    )
    config = tmp_storage.get_trackr_config()
    assert config["enabled"] is True
    assert config["api_url"] == "http://10.0.0.5:8755"


def test_trackr_runtime_state(tmp_storage):
    assert tmp_storage.get_trackr_state() == {}

    tmp_storage.set_trackr_state({
        "connected": True,
        "current": {"artist": "Lane 8", "title": "Brightest Lights"},
    })
    state = tmp_storage.get_trackr_state()
    assert state["connected"] is True
    assert state["current"]["artist"] == "Lane 8"


def test_trackr_runtime_state_isolation(tmp_storage):
    """Returned dict should be a copy, not a reference."""
    tmp_storage.set_trackr_state({"connected": True})
    s1 = tmp_storage.get_trackr_state()
    s1["connected"] = False
    s2 = tmp_storage.get_trackr_state()
    assert s2["connected"] is True


# ── API endpoint tests ────────────────────────────────────────


@pytest.fixture
def api_server(tmp_path, monkeypatch):
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_OPERATOR_KEY", "test-key-123")
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    monkeypatch.setenv("ROONIE_DASHBOARD_ART_PASSWORD", "art-pass-123")
    monkeypatch.setenv("ROONIE_DASHBOARD_JEN_PASSWORD", "jen-pass-123")

    from roonie.dashboard_api.app import create_server
    server = create_server(
        host="127.0.0.1",
        port=0,
        runs_dir=tmp_path / "runs",
    )
    storage = server._roonie_storage
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()

    yield storage, port

    server.shutdown()


def _login(port):
    """Login and return session cookie."""
    import urllib.request
    data = json.dumps({"username": "art", "password": "art-pass-123"}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        set_cookie = resp.headers.get("Set-Cookie", "")
        # Extract the cookie name=value part
        return set_cookie.split(";")[0] if set_cookie else ""


def _api_get(port, path, cookie=None):
    import urllib.request
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _api_patch(port, path, payload, cookie=None):
    import urllib.request
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def test_api_get_trackr_config(api_server):
    storage, port = api_server
    cookie = _login(port)
    body = _api_get(port, "/api/trackr/config", cookie=cookie)
    assert body["enabled"] is False
    assert body["api_url"] == "http://127.0.0.1:8755"


def test_api_get_trackr_status_empty(api_server):
    storage, port = api_server
    cookie = _login(port)
    body = _api_get(port, "/api/trackr/status", cookie=cookie)
    assert body == {}


def test_api_get_trackr_status_with_data(api_server):
    storage, port = api_server
    cookie = _login(port)
    storage.set_trackr_state({"connected": True, "current": {"artist": "Yotto"}})
    body = _api_get(port, "/api/trackr/status", cookie=cookie)
    assert body["connected"] is True
    assert body["current"]["artist"] == "Yotto"


def test_api_patch_trackr_config(api_server):
    storage, port = api_server
    cookie = _login(port)
    body = _api_patch(port, "/api/trackr/config", {"enabled": True, "api_url": "http://10.0.0.5:8755"}, cookie=cookie)
    assert body["ok"] is True
    assert body["trackr_config"]["enabled"] is True
    assert body["trackr_config"]["api_url"] == "http://10.0.0.5:8755"
    # Verify persistence
    config = _api_get(port, "/api/trackr/config", cookie=cookie)
    assert config["enabled"] is True


def test_api_patch_trackr_config_audited(api_server):
    storage, port = api_server
    cookie = _login(port)
    body = _api_patch(port, "/api/trackr/config", {"track_id_skill_enabled": True}, cookie=cookie)
    assert body["ok"] is True
    assert "audit" in body
    assert body["audit"]["action"] == "TRACKR_CONFIG_UPDATE"


# ── Phase B: Enrichment block formatting ──────────────────────


def test_track_enrichment_block_full():
    from roonie.provider_director import ProviderDirector

    metadata = {
        "track_enrichment": {
            "year": 2024,
            "label": "Sudbeat",
            "styles": ["Progressive House", "Deep House"],
            "genres": ["Electronic"],
        }
    }
    block = ProviderDirector._track_enrichment_block(metadata)
    assert "Released 2024 on Sudbeat" in block
    assert "Progressive House" in block


def test_track_enrichment_block_year_only():
    from roonie.provider_director import ProviderDirector

    metadata = {"track_enrichment": {"year": 2019}}
    block = ProviderDirector._track_enrichment_block(metadata)
    assert "Released 2019" in block
    assert "on" not in block


def test_track_enrichment_block_label_only():
    from roonie.provider_director import ProviderDirector

    metadata = {"track_enrichment": {"label": "Anjunadeep"}}
    block = ProviderDirector._track_enrichment_block(metadata)
    assert "Label: Anjunadeep" in block


def test_track_enrichment_block_empty():
    from roonie.provider_director import ProviderDirector

    assert ProviderDirector._track_enrichment_block({}) == ""
    assert ProviderDirector._track_enrichment_block({"track_enrichment": {}}) == ""


def test_previous_track_block():
    from roonie.provider_director import ProviderDirector

    metadata = {
        "previous_track": {
            "raw": "Lane 8 - Brightest Lights",
            "artist": "Lane 8",
            "title": "Brightest Lights",
            "enrichment": {"year": 2021, "label": "This Never Happened", "styles": ["Progressive House"]},
        }
    }
    block = ProviderDirector._previous_track_block(metadata)
    assert "Previous track: Lane 8 - Brightest Lights" in block
    assert "2021 on This Never Happened" in block
    assert "Progressive House" in block


def test_previous_track_block_no_enrichment():
    from roonie.provider_director import ProviderDirector

    metadata = {"previous_track": {"raw": "Artist - Title"}}
    block = ProviderDirector._previous_track_block(metadata)
    assert block == "Previous track: Artist - Title"


def test_previous_track_block_empty():
    from roonie.provider_director import ProviderDirector

    assert ProviderDirector._previous_track_block({}) == ""


def test_enrichment_injected_into_prompt():
    from roonie.prompting import build_roonie_prompt

    prompt = build_roonie_prompt(
        message="what track is this?",
        metadata={"viewer": "fraggy", "channel": "clandestineandcorcyra"},
        now_playing_text="Hernan Cattaneo - Slow Motion",
        enrichment_text="Track info: Released 2023 on Sudbeat. Style: Progressive House.",
        previous_track_text="Previous track: Lane 8 - Brightest Lights (2021 on This Never Happened; Progressive House)",
    )
    assert "Now playing: Hernan Cattaneo - Slow Motion" in prompt
    assert "Track info: Released 2023 on Sudbeat" in prompt
    assert "Previous track: Lane 8 - Brightest Lights" in prompt


def test_enrichment_not_injected_when_empty():
    from roonie.prompting import build_roonie_prompt

    prompt = build_roonie_prompt(
        message="hey roonie",
        metadata={"viewer": "someone", "channel": "test"},
    )
    assert "Track info:" not in prompt
    assert "Previous track:" not in prompt


def test_behavior_guidance_enrichment_available():
    from roonie.behavior_spec import behavior_guidance, CATEGORY_TRACK_ID

    text = behavior_guidance(
        category=CATEGORY_TRACK_ID,
        approved_emotes=[],
        now_playing_available=True,
        enrichment_available=True,
    )
    assert "label, year, style" in text
    assert "database" in text


def test_behavior_guidance_enrichment_not_available():
    from roonie.behavior_spec import behavior_guidance, CATEGORY_TRACK_ID

    text = behavior_guidance(
        category=CATEGORY_TRACK_ID,
        approved_emotes=[],
        now_playing_available=True,
        enrichment_available=False,
    )
    assert "label, year, style" not in text


def test_bridge_enrich_track_without_enricher():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_storage = MagicMock()
    bridge = TrackrBridge(storage=mock_storage)
    bridge._enricher = None
    result = bridge._enrich_track("Artist", "Title")
    assert result == {}


def test_bridge_enrich_track_with_mock_enricher():
    from roonie.control_room.trackr_bridge import TrackrBridge
    from metadata.discogs import DiscogsTrackMeta

    mock_storage = MagicMock()
    bridge = TrackrBridge(storage=mock_storage)
    mock_enricher = MagicMock()
    mock_enricher.enrich_track.return_value = DiscogsTrackMeta(
        release_id=123,
        title="Artist - Title",
        year=2024,
        label="Sudbeat",
        catno="SB-123",
        genres=["Electronic"],
        styles=["Progressive House", "Deep House"],
    )
    bridge._enricher = mock_enricher
    result = bridge._enrich_track("Artist", "Title")
    assert result["year"] == 2024
    assert result["label"] == "Sudbeat"
    assert result["styles"] == ["Progressive House", "Deep House"]
    assert result["catno"] == "SB-123"


def test_bridge_enrichment_stored_in_state():
    from roonie.control_room.trackr_bridge import TrackrBridge
    from metadata.discogs import DiscogsTrackMeta

    mock_storage = MagicMock()
    mock_storage.get_trackr_config.return_value = {"enabled": False}
    bridge = TrackrBridge(storage=mock_storage)
    bridge._current_enrichment = {"year": 2024, "label": "Sudbeat"}
    bridge._previous_enrichment = {"year": 2021, "label": "TNH"}

    bridge._push_state(connected=True, enabled=True, last_current="A - B")
    state = mock_storage.set_trackr_state.call_args[0][0]
    assert state["current_enrichment"] == {"year": 2024, "label": "Sudbeat"}
    assert state["previous_enrichment"] == {"year": 2021, "label": "TNH"}


def test_bridge_enrichment_not_in_state_when_empty():
    from roonie.control_room.trackr_bridge import TrackrBridge

    mock_storage = MagicMock()
    mock_storage.get_trackr_config.return_value = {"enabled": False}
    bridge = TrackrBridge(storage=mock_storage)
    bridge._current_enrichment = {}
    bridge._previous_enrichment = {}

    bridge._push_state(connected=True, enabled=True, last_current="A - B")
    state = mock_storage.set_trackr_state.call_args[0][0]
    assert "current_enrichment" not in state
    assert "previous_enrichment" not in state


def test_music_talk_prompt_mentions_weave_naturally():
    from roonie.prompting import DEFAULT_STYLE

    assert "weave it in naturally" in DEFAULT_STYLE.lower()
    assert "database" in DEFAULT_STYLE.lower()


def test_artist_label_prompt_confirmed_data():
    from roonie.prompting import DEFAULT_STYLE

    assert "CAN name them confidently" in DEFAULT_STYLE
