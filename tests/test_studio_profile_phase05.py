from __future__ import annotations

import json
from pathlib import Path

from roonie.offline_responders import respond
from roonie.types import Event


def test_policy_safe_info_reads_studio_profile_for_gear_and_location(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "studio_profile.json"
    profile = {
        "version": 1,
        "updated_at": "2026-02-12T00:00:00+00:00",
        "updated_by": "jen",
        "location": {"display": "Washington DC area"},
        "social_links": [{"label": "Twitch", "url": "https://twitch.tv/ruleofrune"}],
        "gear": ["Controller: (fill later)", "Interface: (fill later)", "Camera: Sony A7 (fill later)", "DAW: (fill later)"],
        "faq": [{"q": "Where are you based?", "a": "Washington DC area."}],
        "approved_emotes": ["RoonieWave"],
    }
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setenv("ROONIE_STUDIO_PROFILE_PATH", str(profile_path))

    gear_event = Event(event_id="evt-1", message="@roonie what camera are you using?")
    location_event = Event(event_id="evt-2", message="@roonie where are you based?")

    gear_text = respond("responder:policy_safe_info", gear_event, None)
    location_text = respond("responder:policy_safe_info", location_event, None)

    assert gear_text == "Camera: Sony A7 (fill later)."
    assert location_text == "Based in Washington DC area."
