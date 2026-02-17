from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from roonie.dashboard_api.storage import DashboardStorage
from roonie.prompting import build_roonie_prompt
from roonie.provider_director import ProviderDirector


def _make_storage(tmp_path: Path, monkeypatch) -> DashboardStorage:
    monkeypatch.setenv("ROONIE_DASHBOARD_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(tmp_path / "data" / "providers_config.json"))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(tmp_path / "data" / "routing_config.json"))
    return DashboardStorage(runs_dir=tmp_path / "runs")


# ── storage tests ──────────────────────────────────────────────


def test_get_inner_circle_creates_default(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    circle = storage.get_inner_circle()
    assert isinstance(circle, dict)
    assert circle["version"] == 1
    members = circle["members"]
    assert len(members) == 3
    usernames = [m["username"] for m in members]
    assert "cland3stine" in usernames
    assert "c0rcyra" in usernames
    assert "ruleofrune" in usernames


def test_get_inner_circle_returns_deepcopy(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    a = storage.get_inner_circle()
    b = storage.get_inner_circle()
    assert a == b
    a["members"].append({"username": "ghost"})
    c = storage.get_inner_circle()
    assert len(c["members"]) == 3  # original unchanged


def test_update_inner_circle_put(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_inner_circle()
    new_members = [
        {"username": "testuser", "display_name": "Testy", "role": "friend", "note": "A test user."},
    ]
    result, audit = storage.update_inner_circle(
        {"version": 1, "members": new_members},
        actor="art",
    )
    assert len(result["members"]) == 1
    assert result["members"][0]["username"] == "testuser"
    assert result["updated_by"] == "art"
    assert "members" in audit["changed_keys"]


def test_update_inner_circle_patch(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    _ = storage.get_inner_circle()
    new_members = [
        {"username": "newperson", "display_name": "New", "role": "regular", "note": ""},
    ]
    result, _ = storage.update_inner_circle(
        {"members": new_members},
        actor="art",
        patch=True,
    )
    assert len(result["members"]) == 1
    assert result["members"][0]["username"] == "newperson"


def test_inner_circle_username_uniqueness(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    duplicate_members = [
        {"username": "samename", "display_name": "A"},
        {"username": "SameName", "display_name": "B"},
    ]
    try:
        storage.update_inner_circle({"version": 1, "members": duplicate_members}, actor="art")
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "Duplicate" in str(exc)


def test_inner_circle_max_members(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    big_list = [{"username": f"user{i}"} for i in range(51)]
    try:
        storage.update_inner_circle({"version": 1, "members": big_list}, actor="art")
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "maximum" in str(exc).lower()


def test_inner_circle_username_required(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    try:
        storage.update_inner_circle({"version": 1, "members": [{"display_name": "No Username"}]}, actor="art")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_inner_circle_persists_to_disk(tmp_path, monkeypatch):
    storage = _make_storage(tmp_path, monkeypatch)
    storage.update_inner_circle(
        {"version": 1, "members": [{"username": "disktest", "display_name": "Disk"}]},
        actor="art",
    )
    raw = json.loads((tmp_path / "data" / "inner_circle.json").read_text(encoding="utf-8"))
    assert raw["members"][0]["username"] == "disktest"


# ── prompt injection tests ──────────────────────────────────────


def test_inner_circle_block_formats_correctly():
    metadata = {
        "inner_circle": [
            {"username": "cland3stine", "display_name": "Art", "role": "host", "note": "The DJ host."},
            {"username": "viewer1", "display_name": "", "role": "regular", "note": ""},
        ]
    }
    block = ProviderDirector._inner_circle_block(metadata)
    assert "People you know:" in block
    assert "@cland3stine (Art)" in block
    assert "host" in block
    assert "The DJ host." in block
    assert "@viewer1" in block
    assert "regular" in block


def test_inner_circle_block_empty_metadata():
    assert ProviderDirector._inner_circle_block({}) == ""
    assert ProviderDirector._inner_circle_block({"inner_circle": []}) == ""


def test_inner_circle_block_skips_invalid_entries():
    metadata = {
        "inner_circle": [
            {"username": "valid", "display_name": "V", "role": "", "note": ""},
            "not_a_dict",
            {"username": "", "display_name": "Empty", "role": "", "note": ""},
        ]
    }
    block = ProviderDirector._inner_circle_block(metadata)
    assert "@valid" in block
    assert "Empty" not in block


def test_build_roonie_prompt_includes_inner_circle():
    prompt = build_roonie_prompt(
        message="hey roonie",
        metadata={"viewer": "testuser", "channel": "#test"},
        inner_circle_text="People you know:\n- @cland3stine (Art) — host",
    )
    assert "People you know:" in prompt
    assert "@cland3stine (Art)" in prompt


def test_build_roonie_prompt_no_inner_circle():
    prompt = build_roonie_prompt(
        message="hey roonie",
        metadata={"viewer": "testuser", "channel": "#test"},
    )
    assert "People you know:" not in prompt


def test_default_style_no_hardcoded_names():
    from roonie.prompting import DEFAULT_STYLE
    # Art and Jen should no longer be hardcoded with specific usernames in DEFAULT_STYLE
    assert "cland3stine" not in DEFAULT_STYLE
    assert "c0rcyra" not in DEFAULT_STYLE
    # But generic "Your people" section should still exist
    assert "Your people:" in DEFAULT_STYLE
    assert "details are provided separately" in DEFAULT_STYLE
