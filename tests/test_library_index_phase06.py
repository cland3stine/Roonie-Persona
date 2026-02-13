from __future__ import annotations

from pathlib import Path

from roonie.dashboard_api.storage import DashboardStorage
from roonie.offline_responders import respond
from roonie.types import Event


def _fixture_xml_bytes() -> bytes:
    path = Path("tests/fixtures/v1_13_library/rekordbox_sample.xml")
    return path.read_bytes()


def test_library_parse_rebuild_and_confidence_tiers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    storage = DashboardStorage(runs_dir=tmp_path / "runs")

    upload = storage.save_library_xml(_fixture_xml_bytes())
    assert upload["size_bytes"] > 0
    assert upload["xml_hash"]

    rebuilt = storage.rebuild_library_index()
    assert rebuilt["track_count"] == 3
    assert rebuilt["build_ok"] is True
    assert rebuilt["xml_hash"] == upload["xml_hash"]

    exact = storage.search_library_index("Guy J - Lamur")
    close = storage.search_library_index("Guy J Lamur remix")
    none = storage.search_library_index("Nonexistent Artist - Missing Song")

    assert exact["confidence"] == "EXACT"
    assert len(exact["matches"]) >= 1
    assert exact["matches"][0]["artist"] == "Guy J"

    assert close["confidence"] == "CLOSE"
    assert len(close["matches"]) >= 1

    assert none["confidence"] == "NONE"
    assert none["matches"] == []


def test_library_availability_responder_uses_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    storage = DashboardStorage(runs_dir=tmp_path / "runs")
    storage.save_library_xml(_fixture_xml_bytes())
    storage.rebuild_library_index()
    monkeypatch.setenv("ROONIE_LIBRARY_INDEX_PATH", str(tmp_path / "data" / "library" / "library_index.json"))

    exact_event = Event(event_id="evt-1", message="@roonie do you have Guy J - Lamur in your library?")
    close_event = Event(event_id="evt-2", message="@roonie do you have Guy J Lamur remix in your library?")
    none_event = Event(event_id="evt-3", message="@roonie do you have Random Artist - Unknown Track in your library?")

    assert respond("responder:policy_safe_info", exact_event, None) == "Yes — I have that in the library."
    assert respond("responder:policy_safe_info", close_event, None) == "I might have it (close match)."
    assert respond("responder:policy_safe_info", none_event, None) == "Not seeing that in the library."
