from __future__ import annotations

import json
from pathlib import Path

import pytest

from roonie.dashboard_api.storage import DashboardStorage
from roonie.prompting import DEFAULT_STYLE, build_roonie_prompt
from roonie.provider_director import ProviderDirector


def _make_storage(tmp_path: Path, monkeypatch) -> DashboardStorage:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    return DashboardStorage(runs_dir=tmp_path / "runs")


# ── storage tests ──────────────────────────────────────────────


def test_get_stream_schedule_creates_default(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    schedule = storage.get_stream_schedule()
    assert isinstance(schedule, dict)
    assert schedule["version"] == 1
    assert schedule["timezone"] == "ET"
    slots = schedule["slots"]
    assert len(slots) == 2
    days = [s["day"] for s in slots]
    assert "thursday" in days
    assert "saturday" in days


def test_get_stream_schedule_returns_deepcopy(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    a = storage.get_stream_schedule()
    b = storage.get_stream_schedule()
    assert a == b
    a["slots"].append({"day": "friday", "time": "9pm", "note": "", "enabled": True})
    c = storage.get_stream_schedule()
    assert len(c["slots"]) == 2  # original unchanged


def test_update_stream_schedule_put(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_stream_schedule()
    new_slots = [
        {"day": "friday", "time": "9:00 PM", "note": "special", "enabled": True},
    ]
    result, audit = storage.update_stream_schedule(
        {"version": 1, "timezone": "PT", "slots": new_slots, "next_stream_override": ""},
        actor="art",
    )
    assert len(result["slots"]) == 1
    assert result["slots"][0]["day"] == "friday"
    assert result["timezone"] == "PT"
    assert result["updated_by"] == "art"
    assert "slots" in audit["changed_keys"]


def test_update_stream_schedule_patch(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_stream_schedule()
    result, _ = storage.update_stream_schedule(
        {"next_stream_override": "no stream this Saturday"},
        actor="art",
        patch=True,
    )
    assert result["next_stream_override"] == "no stream this Saturday"
    # original slots should be preserved from default via merge
    assert len(result["slots"]) == 2


def test_stream_schedule_day_uniqueness(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    duplicate_slots = [
        {"day": "saturday", "time": "7pm", "note": ""},
        {"day": "Saturday", "time": "9pm", "note": ""},
    ]
    with pytest.raises(ValueError, match="Duplicate"):
        storage.update_stream_schedule(
            {"version": 1, "timezone": "ET", "slots": duplicate_slots},
            actor="art",
        )


def test_stream_schedule_max_7_slots(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    big_list = [{"day": f"day{i}", "time": "7pm"} for i in range(8)]
    with pytest.raises(ValueError, match="maximum"):
        storage.update_stream_schedule(
            {"version": 1, "timezone": "ET", "slots": big_list},
            actor="art",
        )


def test_stream_schedule_invalid_day(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    bad_slots = [{"day": "funday", "time": "7pm", "note": ""}]
    with pytest.raises(ValueError, match="valid weekday"):
        storage.update_stream_schedule(
            {"version": 1, "timezone": "ET", "slots": bad_slots},
            actor="art",
        )


def test_stream_schedule_time_required(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        storage.update_stream_schedule(
            {"version": 1, "timezone": "ET", "slots": [{"day": "monday"}]},
            actor="art",
        )


def test_stream_schedule_persists_to_disk(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_stream_schedule(
        {"version": 1, "timezone": "CT", "slots": [{"day": "monday", "time": "8pm", "note": "test", "enabled": True}]},
        actor="art",
    )
    raw = json.loads((tmp_path / "data" / "stream_schedule.json").read_text(encoding="utf-8"))
    assert raw["slots"][0]["day"] == "monday"
    assert raw["timezone"] == "CT"


def test_stream_schedule_enabled_toggle(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    result, _ = storage.update_stream_schedule(
        {
            "version": 1,
            "timezone": "ET",
            "slots": [
                {"day": "thursday", "time": "7pm", "note": "", "enabled": False},
                {"day": "saturday", "time": "7pm", "note": "", "enabled": True},
            ],
        },
        actor="art",
    )
    assert result["slots"][0]["enabled"] is False
    assert result["slots"][1]["enabled"] is True


def test_stream_schedule_override_field(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    result, _ = storage.update_stream_schedule(
        {
            "version": 1,
            "timezone": "ET",
            "slots": [{"day": "saturday", "time": "7pm", "note": ""}],
            "next_stream_override": "special Friday set at 9pm",
        },
        actor="art",
    )
    assert result["next_stream_override"] == "special Friday set at 9pm"


# ── prompt injection tests ──────────────────────────────────────


def test_stream_schedule_block_formats_correctly():
    metadata = {
        "stream_schedule": {
            "timezone": "ET",
            "slots": [
                {"day": "thursday", "time": "7:00 PM", "note": "Art solo"},
                {"day": "saturday", "time": "7:00 PM", "note": ""},
            ],
            "next_stream_override": "",
        }
    }
    block = ProviderDirector._stream_schedule_block(metadata)
    assert "Stream schedule (all times ET):" in block
    assert "Thursday 7:00 PM (Art solo)" in block
    assert "Saturday 7:00 PM" in block
    assert "Schedule note:" not in block


def test_stream_schedule_block_empty_metadata():
    assert ProviderDirector._stream_schedule_block({}) == ""
    assert ProviderDirector._stream_schedule_block({"stream_schedule": {}}) == ""


def test_stream_schedule_block_enabled_only():
    metadata = {
        "stream_schedule": {
            "timezone": "ET",
            "slots": [
                {"day": "thursday", "time": "7pm", "note": ""},
                {"day": "saturday", "time": "7pm", "note": ""},
            ],
            "next_stream_override": "",
        }
    }
    # All enabled (no "enabled" key means default true in metadata injection)
    block = ProviderDirector._stream_schedule_block(metadata)
    assert "Thursday" in block
    assert "Saturday" in block


def test_stream_schedule_block_with_override():
    metadata = {
        "stream_schedule": {
            "timezone": "ET",
            "slots": [{"day": "saturday", "time": "7pm", "note": ""}],
            "next_stream_override": "no stream this Saturday — back next week",
        }
    }
    block = ProviderDirector._stream_schedule_block(metadata)
    assert "Schedule note: no stream this Saturday" in block


def test_stream_schedule_block_day_order():
    metadata = {
        "stream_schedule": {
            "timezone": "ET",
            "slots": [
                {"day": "saturday", "time": "7pm", "note": ""},
                {"day": "monday", "time": "6pm", "note": ""},
                {"day": "thursday", "time": "7pm", "note": ""},
            ],
            "next_stream_override": "",
        }
    }
    block = ProviderDirector._stream_schedule_block(metadata)
    mon_idx = block.index("Monday")
    thu_idx = block.index("Thursday")
    sat_idx = block.index("Saturday")
    assert mon_idx < thu_idx < sat_idx


def test_build_roonie_prompt_includes_schedule():
    prompt = build_roonie_prompt(
        message="when do you stream?",
        metadata={"viewer": "testuser", "channel": "#test"},
        schedule_text="Stream schedule (all times ET): Thursday 7:00 PM, Saturday 7:00 PM",
    )
    assert "Stream schedule (all times ET):" in prompt
    assert "Thursday 7:00 PM" in prompt


def test_build_roonie_prompt_no_schedule():
    prompt = build_roonie_prompt(
        message="hey roonie",
        metadata={"viewer": "testuser", "channel": "#test"},
    )
    assert "Stream schedule" not in prompt


# ── fabrication guardrail tests ──────────────────────────────────


def test_default_style_contains_schedule_fabrication_guardrail():
    assert "schedules, stream times" in DEFAULT_STYLE


def test_default_style_contains_moment_fabrication_guardrail():
    assert "specific moments from the current set" in DEFAULT_STYLE


def test_default_style_contains_schedule_reference_rule():
    assert "stream schedule provided above" in DEFAULT_STYLE


def test_default_style_contains_variety_nudge():
    assert "same phrasing pattern" in DEFAULT_STYLE
