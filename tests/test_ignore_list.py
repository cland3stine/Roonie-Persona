from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

from roonie.dashboard_api.storage import DashboardStorage


def _make_storage(tmp_path: Path, monkeypatch) -> DashboardStorage:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    return DashboardStorage(runs_dir=tmp_path / "runs")


# ── storage CRUD tests ─────────────────────────────────────────


def test_get_ignore_list_creates_default(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    il = storage.get_ignore_list()
    assert isinstance(il, dict)
    assert il["version"] == 1
    assert il["entries"] == []


def test_get_ignore_list_returns_deepcopy(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    a = storage.get_ignore_list()
    b = storage.get_ignore_list()
    assert a == b
    a["entries"].append({"username": "ghost"})
    c = storage.get_ignore_list()
    assert len(c["entries"]) == 0  # original unchanged


def test_update_ignore_list_put(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_ignore_list()
    new_entries = [
        {"username": "troll123", "reason": "Harassment"},
    ]
    result, audit = storage.update_ignore_list(
        {"version": 1, "entries": new_entries},
        actor="art",
    )
    assert len(result["entries"]) == 1
    assert result["entries"][0]["username"] == "troll123"
    assert result["entries"][0]["reason"] == "Harassment"
    assert result["entries"][0]["added_at"]  # auto-set
    assert result["updated_by"] == "art"
    assert "entries" in audit["changed_keys"]


def test_update_ignore_list_patch(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_ignore_list()
    new_entries = [
        {"username": "baduser", "reason": "Spam"},
    ]
    result, _ = storage.update_ignore_list(
        {"entries": new_entries},
        actor="art",
        patch=True,
    )
    assert len(result["entries"]) == 1
    assert result["entries"][0]["username"] == "baduser"


def test_ignore_list_username_uniqueness(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    duplicate_entries = [
        {"username": "samename", "reason": "A"},
        {"username": "SameName", "reason": "B"},
    ]
    try:
        storage.update_ignore_list({"version": 1, "entries": duplicate_entries}, actor="art")
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "Duplicate" in str(exc)


def test_ignore_list_max_entries(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    big_list = [{"username": f"user{i}"} for i in range(201)]
    try:
        storage.update_ignore_list({"version": 1, "entries": big_list}, actor="art")
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "maximum" in str(exc).lower()


def test_ignore_list_username_required(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    try:
        storage.update_ignore_list({"version": 1, "entries": [{"reason": "No Username"}]}, actor="art")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_ignore_list_persists_to_disk(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_ignore_list(
        {"version": 1, "entries": [{"username": "disktest", "reason": "Test"}]},
        actor="art",
    )
    raw = json.loads((tmp_path / "data" / "ignore_list.json").read_text(encoding="utf-8"))
    assert raw["entries"][0]["username"] == "disktest"


# ── get_ignored_usernames tests ────────────────────────────────


def test_get_ignored_usernames_returns_frozenset(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_ignore_list(
        {"version": 1, "entries": [
            {"username": "TrollUser", "reason": "Bad"},
            {"username": "spammer", "reason": "Spam"},
        ]},
        actor="art",
    )
    result = storage.get_ignored_usernames()
    assert isinstance(result, frozenset)
    assert "trolluser" in result
    assert "spammer" in result


def test_get_ignored_usernames_lowercased(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_ignore_list(
        {"version": 1, "entries": [{"username": "MixedCase"}]},
        actor="art",
    )
    result = storage.get_ignored_usernames()
    assert "mixedcase" in result
    assert "MixedCase" not in result


def test_get_ignored_usernames_empty_default(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    result = storage.get_ignored_usernames()
    assert isinstance(result, frozenset)
    assert len(result) == 0


# ── runtime filtering tests ───────────────────────────────────


def test_bot_constant_skips_processing(tmp_path, monkeypatch):
    """Messages from _IGNORED_BOTS should be skipped before any LLM processing."""
    from roonie.control_room.live_chat import LiveChatBridge, _IGNORED_BOTS
    from twitch.read_path import TwitchMsg

    assert "ror_ai" in _IGNORED_BOTS

    storage = _make_storage(tmp_path, monkeypatch)
    bridge = LiveChatBridge(storage=storage)

    # Track whether _emit_payload_message is called
    called = []
    original = bridge._emit_payload_message
    bridge._emit_payload_message = lambda **kw: called.append(kw) or {"emitted": False, "reason": "test"}

    msg = TwitchMsg(nick="ROR_AI", channel="#test", message="Stream info here", raw=":ROR_AI!ROR_AI@ROR_AI.tmi.twitch.tv PRIVMSG #test :Stream info here", tags={})
    bridge._emit_one(msg, bot_nick="rooniethecat")
    assert len(called) == 0, "Bot message should not reach _emit_payload_message"


def test_ignored_user_skips_processing(tmp_path, monkeypatch):
    """Messages from users on the ignore list should be skipped."""
    from roonie.control_room.live_chat import LiveChatBridge
    from twitch.read_path import TwitchMsg

    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_ignore_list(
        {"version": 1, "entries": [{"username": "trolluser", "reason": "Harassment"}]},
        actor="art",
    )
    bridge = LiveChatBridge(storage=storage)

    called = []
    bridge._emit_payload_message = lambda **kw: called.append(kw) or {"emitted": False, "reason": "test"}

    msg = TwitchMsg(nick="TrollUser", channel="#test", message="hey everyone", raw=":TrollUser PRIVMSG #test :hey everyone", tags={})
    bridge._emit_one(msg, bot_nick="rooniethecat")
    assert len(called) == 0, "Ignored user message should not reach _emit_payload_message"


def test_normal_user_processes(tmp_path, monkeypatch):
    """Normal users should still be processed."""
    from roonie.control_room.live_chat import LiveChatBridge
    from twitch.read_path import TwitchMsg

    storage = _make_storage(tmp_path, monkeypatch)
    bridge = LiveChatBridge(storage=storage)

    called = []
    bridge._emit_payload_message = lambda **kw: called.append(kw) or {"emitted": False, "reason": "test", "event_id": "x", "can_post": False, "blocked_by": []}

    msg = TwitchMsg(nick="NormalViewer", channel="#test", message="hey roonie", raw=":NormalViewer PRIVMSG #test :hey roonie", tags={})
    bridge._emit_one(msg, bot_nick="rooniethecat")
    assert len(called) == 1, "Normal user should reach _emit_payload_message"


def test_fail_open_on_storage_error(tmp_path, monkeypatch):
    """If get_ignored_usernames raises, message should still process (fail-open)."""
    from roonie.control_room.live_chat import LiveChatBridge
    from twitch.read_path import TwitchMsg

    storage = _make_storage(tmp_path, monkeypatch)
    # Monkey-patch to raise
    storage.get_ignored_usernames = lambda: (_ for _ in ()).throw(RuntimeError("storage broken"))

    bridge = LiveChatBridge(storage=storage)

    called = []
    bridge._emit_payload_message = lambda **kw: called.append(kw) or {"emitted": False, "reason": "test", "event_id": "x", "can_post": False, "blocked_by": []}

    msg = TwitchMsg(nick="SomeUser", channel="#test", message="hello", raw=":SomeUser PRIVMSG #test :hello", tags={})
    bridge._emit_one(msg, bot_nick="rooniethecat")
    assert len(called) == 1, "Should fail-open and process the message"


# ── API tests ──────────────────────────────────────────────────


def test_api_get_returns_default(tmp_path, monkeypatch):
    """GET /api/ignore_list should return the default empty list."""
    storage = _make_storage(tmp_path, monkeypatch)
    il = storage.get_ignore_list()
    assert il["version"] == 1
    assert il["entries"] == []
    assert il["updated_by"] == "system"


def test_api_put_updates_and_returns(tmp_path, monkeypatch):
    """PUT /api/ignore_list should update and return the new state."""
    storage = _make_storage(tmp_path, monkeypatch)
    result, audit = storage.update_ignore_list(
        {"version": 1, "entries": [
            {"username": "badactor", "reason": "Trolling"},
            {"username": "spambot", "reason": "Spam links"},
        ]},
        actor="art",
    )
    assert len(result["entries"]) == 2
    assert result["entries"][0]["username"] == "badactor"
    assert result["entries"][1]["username"] == "spambot"
    assert audit["mode"] == "put"

    # Verify via get
    fetched = storage.get_ignore_list()
    assert len(fetched["entries"]) == 2
