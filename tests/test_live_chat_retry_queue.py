from __future__ import annotations

from typing import Any, Dict, List

from roonie.control_room.live_chat import LiveChatBridge
from twitch.read_path import TwitchMsg


class _DummyStorage:
    def get_status(self):  # pragma: no cover - not used in these tests
        class _S:
            def to_dict(self_inner):
                return {}

        return _S()


def test_emit_one_rate_limit_queues_retry(monkeypatch) -> None:
    bridge = LiveChatBridge(storage=_DummyStorage(), account="bot")

    queued: List[Dict[str, Any]] = []

    def _fake_emit(**kwargs):
        return {
            "event_id": "evt-1",
            "emitted": False,
            "reason": "RATE_LIMIT",
            "can_post": True,
            "blocked_by": [],
        }

    def _fake_queue_retry(**kwargs):
        queued.append(dict(kwargs))

    monkeypatch.setattr(bridge, "_emit_payload_message", _fake_emit)
    monkeypatch.setattr(bridge, "_queue_retry", _fake_queue_retry)

    msg = TwitchMsg(nick="cland3stine", channel="ruleofrune", message="@RoonieTheCat hey", raw="")
    bridge._emit_one(msg, bot_nick="rooniethecat")

    assert len(queued) == 1
    assert queued[0]["attempt"] == 1
    assert queued[0]["actor"] == "cland3stine"
    assert queued[0]["message"] == "@RoonieTheCat hey"


def test_process_retry_item_requeues_on_rate_limit(monkeypatch) -> None:
    bridge = LiveChatBridge(storage=_DummyStorage(), account="bot")

    queued: List[Dict[str, Any]] = []

    def _fake_emit(**kwargs):
        return {
            "event_id": "evt-2",
            "emitted": False,
            "reason": "RATE_LIMIT",
            "can_post": True,
            "blocked_by": [],
        }

    def _fake_queue_retry(**kwargs):
        queued.append(dict(kwargs))

    monkeypatch.setattr(bridge, "_emit_payload_message", _fake_emit)
    monkeypatch.setattr(bridge, "_queue_retry", _fake_queue_retry)

    bridge._process_retry_item(
        {
            "actor": "cland3stine",
            "message": "@RoonieTheCat hey",
            "channel": "ruleofrune",
            "is_direct_mention": True,
            "attempt": 1,
            "metadata_extra": None,
        }
    )

    assert len(queued) == 1
    assert queued[0]["attempt"] == 2


def test_process_retry_item_does_not_requeue_after_emit(monkeypatch) -> None:
    bridge = LiveChatBridge(storage=_DummyStorage(), account="bot")

    queued: List[Dict[str, Any]] = []

    def _fake_emit(**kwargs):
        return {
            "event_id": "evt-3",
            "emitted": True,
            "reason": "EMITTED",
            "can_post": True,
            "blocked_by": [],
        }

    def _fake_queue_retry(**kwargs):
        queued.append(dict(kwargs))

    monkeypatch.setattr(bridge, "_emit_payload_message", _fake_emit)
    monkeypatch.setattr(bridge, "_queue_retry", _fake_queue_retry)

    bridge._process_retry_item(
        {
            "actor": "cland3stine",
            "message": "@RoonieTheCat hey",
            "channel": "ruleofrune",
            "is_direct_mention": True,
            "attempt": 2,
            "metadata_extra": None,
        }
    )

    assert queued == []
