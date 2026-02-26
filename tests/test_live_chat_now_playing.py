"""Tests for now-playing metadata injection via TRACKR API state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from roonie.control_room.live_chat import LiveChatBridge


class _DummyStorage:
    def __init__(self, trackr_state=None):
        self._trackr_state = trackr_state or {}

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

    def get_trackr_state(self):
        return dict(self._trackr_state)


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


def test_emit_payload_injects_now_playing_from_trackr_state(tmp_path: Path, monkeypatch) -> None:
    trackr_state = {
        "connected": True,
        "current": {"raw": "Hernan Cattaneo - Slow Motion", "artist": "Hernan Cattaneo", "title": "Slow Motion"},
        "current_enrichment": {"year": 2023, "label": "Sudbeat", "styles": ["Progressive House"]},
        "previous": {"raw": "Lane 8 - Brightest Lights", "artist": "Lane 8", "title": "Brightest Lights"},
        "previous_enrichment": {"year": 2021, "label": "This Never Happened"},
    }

    captured: Dict[str, Any] = {}
    monkeypatch.setattr("roonie.control_room.live_chat.run_payload", _stub_run_payload(tmp_path, captured))

    bridge = LiveChatBridge(storage=_DummyStorage(trackr_state), account="bot")
    _ = bridge._emit_payload_message(
        actor="alice",
        message="@RoonieTheCat what track is this",
        channel="clandestineandcorcyra",
        is_direct_mention=True,
        metadata_extra=None,
    )

    metadata = captured["payload"]["inputs"][0]["metadata"]
    assert metadata["now_playing"] == "Hernan Cattaneo - Slow Motion"
    assert metadata["track_line"] == "Hernan Cattaneo - Slow Motion"
    assert metadata["track_enrichment"]["year"] == 2023
    assert metadata["track_enrichment"]["label"] == "Sudbeat"
    assert metadata["previous_track"]["raw"] == "Lane 8 - Brightest Lights"
    assert metadata["previous_track"]["enrichment"]["year"] == 2021


def test_emit_payload_no_now_playing_when_trackr_disconnected(tmp_path: Path, monkeypatch) -> None:
    trackr_state = {"connected": False}

    captured: Dict[str, Any] = {}
    monkeypatch.setattr("roonie.control_room.live_chat.run_payload", _stub_run_payload(tmp_path, captured))

    bridge = LiveChatBridge(storage=_DummyStorage(trackr_state), account="bot")
    _ = bridge._emit_payload_message(
        actor="alice",
        message="@RoonieTheCat hey",
        channel="clandestineandcorcyra",
        is_direct_mention=True,
        metadata_extra=None,
    )

    metadata = captured["payload"]["inputs"][0]["metadata"]
    assert "now_playing" not in metadata
    assert "track_line" not in metadata
    assert "track_enrichment" not in metadata


def test_emit_payload_no_now_playing_when_no_trackr_state(tmp_path: Path, monkeypatch) -> None:
    captured: Dict[str, Any] = {}
    monkeypatch.setattr("roonie.control_room.live_chat.run_payload", _stub_run_payload(tmp_path, captured))

    bridge = LiveChatBridge(storage=_DummyStorage({}), account="bot")
    _ = bridge._emit_payload_message(
        actor="alice",
        message="@RoonieTheCat hey",
        channel="clandestineandcorcyra",
        is_direct_mention=True,
        metadata_extra=None,
    )

    metadata = captured["payload"]["inputs"][0]["metadata"]
    assert "now_playing" not in metadata
    assert "track_enrichment" not in metadata
