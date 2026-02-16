from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


def _write_library_index(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tracks": [
            {
                "artist": "Maze 28",
                "title": "Midnight Pattern",
                "mix": "",
                "search_key": "maze 28 midnight pattern",
            },
            {
                "artist": "Maze 28",
                "title": "Midnight Pattern",
                "mix": "Extended Mix",
                "search_key": "maze 28 midnight pattern extended mix",
            },
            {
                "artist": "Other Artist",
                "title": "Unrelated Track",
                "mix": "",
                "search_key": "other artist unrelated track",
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_music_fact_question_includes_library_grounding_and_hedge_policy(tmp_path: Path, monkeypatch) -> None:
    lib_path = tmp_path / "library_index.json"
    _write_library_index(lib_path)
    monkeypatch.setenv("ROONIE_LIBRARY_INDEX_PATH", str(lib_path))

    captured: Dict[str, Any] = {}

    def _stub_route_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        kwargs["context"]["provider_selected"] = "grok"
        kwargs["context"]["moderation_result"] = "allow"
        return "ok"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    env = Env(offline=False)

    # Seed topic anchor (artist name).
    director.evaluate(
        Event(
            event_id="evt-1",
            message="@RoonieTheCat have you heard the latest Maze 28 release?",
            metadata={"user": "cland3stine", "is_direct_mention": True, "mode": "live", "session_id": "lib-grounding"},
        ),
        env,
    )
    director.evaluate(
        Event(
            event_id="evt-2",
            message="@RoonieTheCat what label was it out on?",
            metadata={"user": "cland3stine", "is_direct_mention": True, "mode": "live", "session_id": "lib-grounding"},
        ),
        env,
    )

    prompt = str(captured.get("prompt") or "")
    assert "Library grounding (local)" in prompt
    assert "Maze 28 - Midnight Pattern" in prompt
    assert "Music facts policy:" in prompt


def test_no_library_file_still_includes_no_matches_block(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing_library_index.json"
    monkeypatch.setenv("ROONIE_LIBRARY_INDEX_PATH", str(missing))

    captured: Dict[str, Any] = {}

    def _stub_route_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        kwargs["context"]["provider_selected"] = "grok"
        kwargs["context"]["moderation_result"] = "allow"
        return "ok"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub_route_generate)

    director = ProviderDirector()
    _ = director.evaluate(
        Event(
            event_id="evt-1",
            message="@RoonieTheCat what label was it out on?",
            metadata={"user": "cland3stine", "is_direct_mention": True, "mode": "live", "session_id": "lib-missing"},
        ),
        Env(offline=False),
    )

    prompt = str(captured.get("prompt") or "")
    assert "Library grounding (local): no close matches." in prompt
    assert "Music facts policy:" in prompt

