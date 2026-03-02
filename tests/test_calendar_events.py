"""Calendar events — storage, recurrence, API, migration, and prompt integration tests."""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from src.roonie.dashboard_api.storage import DashboardStorage


@pytest.fixture
def storage(tmp_path: Path) -> DashboardStorage:
    runs = tmp_path / "runs"
    runs.mkdir()
    s = DashboardStorage(runs_dir=runs)
    s.data_dir = tmp_path / "data"
    s.data_dir.mkdir(exist_ok=True)
    s._calendar_events_path = s.data_dir / "calendar_events.json"
    s._stream_schedule_path = s.data_dir / "stream_schedule.json"
    return s


# ── CRUD tests ──────────────────────────────────────────────────


def test_create_calendar_event(storage: DashboardStorage):
    payload = {
        "title": "Saturday Stream",
        "date": "2026-03-07",
        "start_time": "7:00 PM",
        "end_time": "11:00 PM",
        "category": "stream",
    }
    event, audit = storage.create_calendar_event(payload, actor="art")
    assert event["title"] == "Saturday Stream"
    assert event["date"] == "2026-03-07"
    assert event["category"] == "stream"
    assert event["id"]
    assert event["created_by"] == "art"
    assert audit["action"] == "create"
    assert audit["event_id"] == event["id"]


def test_get_calendar_event(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event(
        {"title": "Test", "date": "2026-03-07", "start_time": "7PM", "category": "stream"},
        actor="art",
    )
    fetched = storage.get_calendar_event(ev["id"])
    assert fetched is not None
    assert fetched["title"] == "Test"
    assert fetched["id"] == ev["id"]


def test_get_calendar_event_not_found(storage: DashboardStorage):
    assert storage.get_calendar_event("nonexistent-id") is None


def test_update_calendar_event_put(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event(
        {"title": "Old Title", "date": "2026-03-07", "start_time": "7PM", "category": "stream"},
        actor="art",
    )
    updated, audit = storage.update_calendar_event(
        ev["id"],
        {"title": "New Title", "date": "2026-03-07", "start_time": "8PM", "category": "content"},
        actor="jen",
        patch=False,
    )
    assert updated["title"] == "New Title"
    assert updated["category"] == "content"
    assert updated["updated_by"] == "jen"
    assert "title" in audit["changed_keys"]


def test_update_calendar_event_patch(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event(
        {"title": "Keep Me", "date": "2026-03-07", "start_time": "7PM", "category": "stream"},
        actor="art",
    )
    updated, _ = storage.update_calendar_event(
        ev["id"],
        {"description": "Added desc"},
        actor="art",
        patch=True,
    )
    assert updated["title"] == "Keep Me"
    assert updated["description"] == "Added desc"


def test_update_nonexistent_event(storage: DashboardStorage):
    with pytest.raises(ValueError, match="not found"):
        storage.update_calendar_event("fake-id", {"title": "X", "date": "2026-01-01", "start_time": "", "category": "stream"}, actor="a")


def test_delete_calendar_event(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event(
        {"title": "Delete Me", "date": "2026-03-07", "start_time": "7PM", "category": "stream"},
        actor="art",
    )
    result = storage.delete_calendar_event(ev["id"], actor="art")
    assert result["event_id"] == ev["id"]
    assert result["action"] == "delete"
    assert storage.get_calendar_event(ev["id"]) is None


def test_delete_nonexistent_event(storage: DashboardStorage):
    with pytest.raises(ValueError, match="not found"):
        storage.delete_calendar_event("fake-id", actor="a")


def test_get_calendar_events_no_range(storage: DashboardStorage):
    storage.create_calendar_event(
        {"title": "A", "date": "2026-03-01", "start_time": "7PM", "category": "stream"}, actor="a",
    )
    storage.create_calendar_event(
        {"title": "B", "date": "2026-03-15", "start_time": "8PM", "category": "content"}, actor="a",
    )
    result = storage.get_calendar_events()
    assert len(result["events"]) == 2


def test_get_calendar_events_with_range(storage: DashboardStorage):
    storage.create_calendar_event(
        {"title": "In Range", "date": "2026-03-10", "start_time": "7PM", "category": "stream"}, actor="a",
    )
    storage.create_calendar_event(
        {"title": "Out of Range", "date": "2026-04-10", "start_time": "7PM", "category": "stream"}, actor="a",
    )
    result = storage.get_calendar_events(start_date="2026-03-01", end_date="2026-03-31")
    titles = [e["title"] for e in result["events"]]
    assert "In Range" in titles
    assert "Out of Range" not in titles


def test_event_limit_500(storage: DashboardStorage):
    for i in range(500):
        storage.create_calendar_event(
            {"title": f"Event {i}", "date": "2026-03-01", "start_time": "", "category": "stream"}, actor="a",
        )
    with pytest.raises(ValueError, match="limit"):
        storage.create_calendar_event(
            {"title": "One Too Many", "date": "2026-03-01", "start_time": "", "category": "stream"}, actor="a",
        )


# ── Validation tests ────────────────────────────────────────────


def test_validate_title_required(storage: DashboardStorage):
    with pytest.raises(ValueError):
        storage.create_calendar_event(
            {"title": "", "date": "2026-03-07", "start_time": "7PM", "category": "stream"}, actor="a",
        )


def test_validate_date_format(storage: DashboardStorage):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        storage.create_calendar_event(
            {"title": "Bad Date", "date": "March 7", "start_time": "7PM", "category": "stream"}, actor="a",
        )


def test_validate_category(storage: DashboardStorage):
    with pytest.raises(ValueError, match="category"):
        storage.create_calendar_event(
            {"title": "Bad Cat", "date": "2026-03-07", "start_time": "", "category": "invalid"}, actor="a",
        )


def test_valid_categories(storage: DashboardStorage):
    for cat in ("stream", "content", "community", "personal"):
        ev, _ = storage.create_calendar_event(
            {"title": f"Cat {cat}", "date": "2026-03-07", "start_time": "", "category": cat}, actor="a",
        )
        assert ev["category"] == cat


def test_assigned_to_defaults_both(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event(
        {"title": "No Assignee", "date": "2026-03-07", "start_time": "", "category": "stream"}, actor="a",
    )
    assert ev["assigned_to"] == "both"


def test_assigned_to_values(storage: DashboardStorage):
    for val in ("art", "jen", "both"):
        ev, _ = storage.create_calendar_event(
            {"title": f"Assigned {val}", "date": "2026-03-07", "start_time": "", "category": "stream", "assigned_to": val},
            actor="a",
        )
        assert ev["assigned_to"] == val


# ── Stream-specific fields ──────────────────────────────────────


def test_stream_fields(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Theme Night",
        "date": "2026-03-07",
        "start_time": "7PM",
        "category": "stream",
        "theme": "Deep Progressive",
        "genre_focus": "Progressive House",
        "guests": ["DJ Guest"],
        "pre_stream_notes": "Test the new visualizer",
        "post_stream_notes": "Went great!",
    }, actor="art")
    assert ev["theme"] == "Deep Progressive"
    assert ev["genre_focus"] == "Progressive House"
    assert ev["guests"] == ["DJ Guest"]
    assert ev["pre_stream_notes"] == "Test the new visualizer"
    assert ev["post_stream_notes"] == "Went great!"


def test_guests_from_comma_string(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Guests", "date": "2026-03-07", "start_time": "", "category": "stream",
        "guests": "DJ Alpha, DJ Beta, DJ Gamma",
    }, actor="a")
    assert ev["guests"] == ["DJ Alpha", "DJ Beta", "DJ Gamma"]


def test_guests_cap_at_10(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Many Guests", "date": "2026-03-07", "start_time": "", "category": "stream",
        "guests": [f"Guest {i}" for i in range(15)],
    }, actor="a")
    assert len(ev["guests"]) == 10


# ── RRULE validation ────────────────────────────────────────────


def test_rrule_weekly(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Weekly Stream", "date": "2026-03-07", "start_time": "7PM", "category": "stream",
        "rrule": {"freq": "WEEKLY", "interval": 1, "byday": ["SA"]},
    }, actor="a")
    assert ev["rrule"]["freq"] == "WEEKLY"
    assert ev["rrule"]["byday"] == ["SA"]


def test_rrule_daily(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Daily", "date": "2026-03-01", "start_time": "", "category": "content",
        "rrule": {"freq": "DAILY", "interval": 1},
    }, actor="a")
    assert ev["rrule"]["freq"] == "DAILY"


def test_rrule_invalid_freq(storage: DashboardStorage):
    with pytest.raises(ValueError, match="freq"):
        storage.create_calendar_event({
            "title": "Bad", "date": "2026-03-07", "start_time": "", "category": "stream",
            "rrule": {"freq": "MONTHLY"},
        }, actor="a")


def test_rrule_with_until(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Until", "date": "2026-03-01", "start_time": "", "category": "stream",
        "rrule": {"freq": "WEEKLY", "byday": ["TH"], "until": "2026-06-30"},
    }, actor="a")
    assert ev["rrule"]["until"] == "2026-06-30"


def test_rrule_with_count(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Counted", "date": "2026-03-01", "start_time": "", "category": "stream",
        "rrule": {"freq": "WEEKLY", "byday": ["SA"], "count": 10},
    }, actor="a")
    assert ev["rrule"]["count"] == 10


def test_rrule_null_is_no_recurrence(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "One Off", "date": "2026-03-07", "start_time": "", "category": "stream",
        "rrule": None,
    }, actor="a")
    assert ev["rrule"] is None


def test_rrule_byday_string_parsed(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Byday Str", "date": "2026-03-07", "start_time": "", "category": "stream",
        "rrule": {"freq": "WEEKLY", "byday": "TH, SA"},
    }, actor="a")
    assert ev["rrule"]["byday"] == ["TH", "SA"]


# ── Recurrence expansion tests ──────────────────────────────────


def test_expand_weekly_saturday():
    event = {
        "title": "Saturday Stream",
        "date": "2026-03-07",
        "start_time": "7PM",
        "category": "stream",
        "rrule": {"freq": "WEEKLY", "interval": 1, "byday": ["SA"]},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-31")
    dates = [o["date"] for o in occs]
    assert "2026-03-07" in dates
    assert "2026-03-14" in dates
    assert "2026-03-21" in dates
    assert "2026-03-28" in dates
    assert len(dates) == 4


def test_expand_weekly_two_days():
    event = {
        "title": "Biweekly",
        "date": "2026-03-01",
        "start_time": "7PM",
        "category": "stream",
        "rrule": {"freq": "WEEKLY", "interval": 1, "byday": ["TH", "SA"]},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-15")
    dates = sorted(o["date"] for o in occs)
    # Thu Mar 5, Sat Mar 7, Thu Mar 12, Sat Mar 14
    assert "2026-03-05" in dates
    assert "2026-03-07" in dates
    assert "2026-03-12" in dates
    assert "2026-03-14" in dates


def test_expand_daily():
    event = {
        "title": "Daily Content",
        "date": "2026-03-01",
        "start_time": "",
        "category": "content",
        "rrule": {"freq": "DAILY", "interval": 1},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-07")
    assert len(occs) == 7


def test_expand_daily_with_interval():
    event = {
        "title": "Every 3 Days",
        "date": "2026-03-01",
        "start_time": "",
        "category": "content",
        "rrule": {"freq": "DAILY", "interval": 3},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-31")
    dates = [o["date"] for o in occs]
    assert "2026-03-01" in dates
    assert "2026-03-04" in dates
    assert "2026-03-07" in dates
    assert "2026-03-02" not in dates


def test_expand_with_until():
    event = {
        "title": "Until Mid-March",
        "date": "2026-03-01",
        "start_time": "",
        "category": "stream",
        "rrule": {"freq": "WEEKLY", "interval": 1, "byday": ["SA"], "until": "2026-03-15"},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-31")
    dates = [o["date"] for o in occs]
    assert "2026-03-07" in dates
    assert "2026-03-14" in dates
    assert "2026-03-21" not in dates


def test_expand_with_count():
    event = {
        "title": "Only 2",
        "date": "2026-03-07",
        "start_time": "",
        "category": "stream",
        "rrule": {"freq": "WEEKLY", "interval": 1, "byday": ["SA"], "count": 2},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-12-31")
    assert len(occs) == 2


def test_expand_no_rrule_in_range():
    event = {"title": "Single", "date": "2026-03-15", "start_time": "", "category": "stream"}
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-31")
    assert len(occs) == 1
    assert occs[0]["date"] == "2026-03-15"


def test_expand_no_rrule_out_of_range():
    event = {"title": "Single", "date": "2026-04-15", "start_time": "", "category": "stream"}
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-31")
    assert len(occs) == 0


def test_expand_weekly_no_byday():
    """WEEKLY without byday repeats on the same weekday as the start date."""
    event = {
        "title": "No Byday",
        "date": "2026-03-04",  # Wednesday
        "start_time": "",
        "category": "stream",
        "rrule": {"freq": "WEEKLY", "interval": 1},
    }
    occs = DashboardStorage.expand_rrule_occurrences(event, "2026-03-01", "2026-03-31")
    dates = [o["date"] for o in occs]
    # Should be every Wednesday: Mar 4, 11, 18, 25
    assert "2026-03-04" in dates
    assert "2026-03-11" in dates
    assert "2026-03-18" in dates
    assert "2026-03-25" in dates
    assert len(dates) == 4


# ── Migration tests ─────────────────────────────────────────────


def test_migrate_weekly_schedule(storage: DashboardStorage):
    # Set up a weekly schedule
    storage.update_stream_schedule({
        "timezone": "ET",
        "slots": [
            {"day": "thursday", "time": "7:00 PM", "note": "Art solo", "enabled": True},
            {"day": "saturday", "time": "7:00 PM", "note": "", "enabled": True},
        ],
    }, actor="art")
    result = storage.migrate_weekly_schedule_to_calendar(actor="art")
    assert result["migrated"] == 2
    assert result["skipped"] is False
    events = storage.get_calendar_events()["events"]
    assert len(events) == 2
    # All should be recurring stream events
    for eid, ev in events.items():
        assert ev["category"] == "stream"
        assert ev["rrule"] is not None
        assert ev["rrule"]["freq"] == "WEEKLY"


def test_migrate_idempotent(storage: DashboardStorage):
    storage.update_stream_schedule({
        "timezone": "ET",
        "slots": [{"day": "saturday", "time": "7:00 PM", "note": "", "enabled": True}],
    }, actor="art")
    storage.migrate_weekly_schedule_to_calendar(actor="art")
    result2 = storage.migrate_weekly_schedule_to_calendar(actor="art")
    assert result2["skipped"] is True
    assert result2["migrated"] == 0


def test_migrate_disabled_slot(storage: DashboardStorage):
    storage.update_stream_schedule({
        "timezone": "ET",
        "slots": [{"day": "thursday", "time": "7:00 PM", "note": "", "enabled": False}],
    }, actor="art")
    result = storage.migrate_weekly_schedule_to_calendar(actor="art")
    assert result["migrated"] == 1
    events = storage.get_calendar_events()["events"]
    ev = list(events.values())[0]
    assert ev["enabled"] is False


# ── Prompt integration tests ────────────────────────────────────


def test_prompt_data_empty(storage: DashboardStorage):
    result = storage.get_calendar_events_for_prompt()
    assert result == {}


def test_prompt_data_upcoming_streams(storage: DashboardStorage):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    storage.create_calendar_event({
        "title": "Tonight's Stream",
        "date": today,
        "start_time": "7:00 PM",
        "category": "stream",
        "theme": "Deep Prog Night",
        "pre_stream_notes": "Test new overlay",
    }, actor="art")
    result = storage.get_calendar_events_for_prompt()
    assert "upcoming_streams" in result
    assert len(result["upcoming_streams"]) == 1
    assert result["upcoming_streams"][0]["theme"] == "Deep Prog Night"
    assert result["today_pre_stream_notes"] == "Test new overlay"
    assert result["today_theme"] == "Deep Prog Night"


def test_prompt_data_community_events(storage: DashboardStorage):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    storage.create_calendar_event({
        "title": "Fraggy's Birthday",
        "date": today,
        "start_time": "",
        "category": "community",
    }, actor="art")
    result = storage.get_calendar_events_for_prompt()
    assert "today_community" in result
    assert "Fraggy's Birthday" in result["today_community"]


def test_prompt_data_disabled_excluded(storage: DashboardStorage):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    storage.create_calendar_event({
        "title": "Disabled Stream",
        "date": today,
        "start_time": "7PM",
        "category": "stream",
        "enabled": False,
    }, actor="art")
    result = storage.get_calendar_events_for_prompt()
    assert result == {}


def test_prompt_data_recurring_streams(storage: DashboardStorage):
    """Recurring stream events should appear in prompt data."""
    today = datetime.now(timezone.utc)
    # Create a recurring event starting from today
    storage.create_calendar_event({
        "title": "Weekly Stream",
        "date": today.strftime("%Y-%m-%d"),
        "start_time": "7:00 PM",
        "category": "stream",
        "rrule": {"freq": "DAILY", "interval": 1, "count": 7},
    }, actor="art")
    result = storage.get_calendar_events_for_prompt()
    assert "upcoming_streams" in result
    assert len(result["upcoming_streams"]) >= 1


# ── Edge cases ──────────────────────────────────────────────────


def test_empty_calendar_returns_default_structure(storage: DashboardStorage):
    result = storage.get_calendar_events()
    assert result["version"] == 1
    assert result["events"] == {}


def test_enabled_defaults_true(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Default Enabled", "date": "2026-03-07", "start_time": "", "category": "stream",
    }, actor="a")
    assert ev["enabled"] is True


def test_preserve_created_at_on_update(storage: DashboardStorage):
    ev, _ = storage.create_calendar_event({
        "title": "Original", "date": "2026-03-07", "start_time": "", "category": "stream",
    }, actor="art")
    original_created = ev["created_at"]
    updated, _ = storage.update_calendar_event(
        ev["id"],
        {"title": "Updated", "date": "2026-03-07", "start_time": "", "category": "stream"},
        actor="jen",
    )
    assert updated["created_at"] == original_created
    assert updated["created_by"] == "art"
    assert updated["updated_by"] == "jen"


# ── Provider Director prompt block tests ────────────────────────

from src.roonie.provider_director import ProviderDirector


def test_calendar_schedule_block_empty():
    assert ProviderDirector._calendar_schedule_block({}) == ""
    assert ProviderDirector._calendar_schedule_block({"calendar_prompt_data": {}}) == ""


def test_calendar_schedule_block_with_streams():
    metadata = {
        "current_time_local_iso": "2026-03-07T19:00:00-05:00",
        "stream_schedule": {"timezone": "ET", "slots": [], "next_stream_override": ""},
        "calendar_prompt_data": {
            "upcoming_streams": [
                {"date": "2026-03-07", "title": "Saturday Stream", "start_time": "7:00 PM", "theme": "Deep Prog", "genre_focus": "", "guests": []},
                {"date": "2026-03-12", "title": "Thursday Session", "start_time": "7:00 PM", "theme": "", "genre_focus": "", "guests": []},
            ],
            "today_theme": "Deep Prog",
            "today_pre_stream_notes": "Test the new visualizer overlay",
        },
    }
    block = ProviderDirector._calendar_schedule_block(metadata)
    assert "Current local time (ET):" in block
    assert "Upcoming streams:" in block
    assert "Saturday Stream" in block
    assert "Deep Prog" in block
    assert "Tonight's theme: Deep Prog" in block
    assert "Stream notes: Test the new visualizer overlay" in block


def test_calendar_schedule_block_with_community():
    metadata = {
        "calendar_prompt_data": {
            "today_community": ["Fraggy's Birthday", "Viewer Milestone"],
        },
    }
    block = ProviderDirector._calendar_schedule_block(metadata)
    assert "Today's community events:" in block
    assert "Fraggy's Birthday" in block


def test_calendar_schedule_block_preserves_override():
    metadata = {
        "stream_schedule": {"timezone": "ET", "slots": [], "next_stream_override": "no stream this Thursday"},
        "calendar_prompt_data": {
            "upcoming_streams": [{"date": "2026-03-07", "title": "Sat", "start_time": "7PM", "theme": "", "genre_focus": "", "guests": []}],
        },
    }
    block = ProviderDirector._calendar_schedule_block(metadata)
    assert "Schedule note: no stream this Thursday" in block


def test_calendar_block_falls_back_to_legacy():
    """When no calendar data, schedule_text should fall back to legacy _stream_schedule_block."""
    metadata = {
        "current_time_local_iso": "2026-03-07T19:00:00-05:00",
        "stream_schedule": {
            "timezone": "ET",
            "slots": [
                {"day": "thursday", "time": "7:00 PM", "note": "Art solo", "enabled": True},
                {"day": "saturday", "time": "7:00 PM", "note": "", "enabled": True},
            ],
            "next_stream_override": "",
        },
    }
    # calendar_schedule_block returns empty for no calendar data
    cal_block = ProviderDirector._calendar_schedule_block(metadata)
    assert cal_block == ""
    # legacy should still work
    legacy_block = ProviderDirector._stream_schedule_block(metadata)
    assert "Thursday 7:00 PM" in legacy_block
    assert "Saturday 7:00 PM" in legacy_block
