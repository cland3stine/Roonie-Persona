from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from roonie.control_room.live_chat import LiveChatBridge


class _DummyStorage:
    def get_status(self):
        class _S:
            def to_dict(self_inner):
                return {
                    "can_post": True,
                    "blocked_by": [],
                    "active_director": "ProviderDirector",
                    "routing_enabled": True,
                    "session_id": "sess-1",
                }

        return _S()


def _stub_run_payload(tmp_path: Path, captured: Dict[str, Any]):
    def _fake_run_payload(payload: Dict[str, Any], **_kwargs):
        captured["payload"] = payload
        event_id = str(payload["inputs"][0]["event_id"])
        run_path = tmp_path / "run_doc.json"
        run_path.write_text(
            json.dumps(
                {
                    "outputs": [
                        {
                            "event_id": event_id,
                            "emitted": False,
                            "reason": "NOOP",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return run_path

    return _fake_run_payload


def test_emit_payload_injects_now_playing_from_explicit_path(tmp_path: Path, monkeypatch) -> None:
    now_playing_path = tmp_path / "overlay" / "nowplaying_chat.txt"
    now_playing_path.parent.mkdir(parents=True, exist_ok=True)
    now_playing_path.write_text("Now Playing: Artist A - Track One\n", encoding="utf-8")
    monkeypatch.setenv("ROONIE_NOW_PLAYING_PATH", str(now_playing_path))

    captured: Dict[str, Any] = {}
    monkeypatch.setattr("roonie.control_room.live_chat.run_payload", _stub_run_payload(tmp_path, captured))

    bridge = LiveChatBridge(storage=_DummyStorage(), account="bot")
    _ = bridge._emit_payload_message(
        actor="alice",
        message="@RoonieTheCat what track is this",
        channel="ruleofrune",
        is_direct_mention=True,
        metadata_extra=None,
    )

    metadata = captured["payload"]["inputs"][0]["metadata"]
    assert metadata["now_playing"] == "Now Playing: Artist A - Track One"
    assert metadata["track_line"] == "Now Playing: Artist A - Track One"


def test_emit_payload_skips_now_playing_when_file_is_missing(tmp_path: Path, monkeypatch) -> None:
    missing_path = tmp_path / "overlay" / "does_not_exist.txt"
    monkeypatch.setenv("ROONIE_NOW_PLAYING_PATH", str(missing_path))

    captured: Dict[str, Any] = {}
    monkeypatch.setattr("roonie.control_room.live_chat.run_payload", _stub_run_payload(tmp_path, captured))

    bridge = LiveChatBridge(storage=_DummyStorage(), account="bot")
    _ = bridge._emit_payload_message(
        actor="alice",
        message="@RoonieTheCat hey",
        channel="ruleofrune",
        is_direct_mention=True,
        metadata_extra=None,
    )

    metadata = captured["payload"]["inputs"][0]["metadata"]
    assert "now_playing" not in metadata
    assert "track_line" not in metadata
