from __future__ import annotations

import json
from pathlib import Path


def _load(name: str) -> dict:
    p = Path("tests/fixtures/v1_10h_activation") / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_disarmed_denies():
    from src.activation.state import decide_activation

    cfg = _load("case_disarmed.json")
    d = decide_activation(cfg["system"], cfg["presence_decision"])
    assert d.allowed is False
    assert "disarmed" in d.reason.lower()


def test_kill_switch_denies():
    from src.activation.state import decide_activation

    cfg = _load("case_kill_switch_on.json")
    d = decide_activation(cfg["system"], cfg["presence_decision"])
    assert d.allowed is False
    assert "kill" in d.reason.lower()


def test_armed_and_presence_allowed_allows():
    from src.activation.state import decide_activation

    cfg = _load("case_armed_and_allowed.json")
    d = decide_activation(cfg["system"], cfg["presence_decision"])
    assert d.allowed is True
    assert d.lane == "ambient"


def test_presence_denied_denies():
    from src.activation.state import decide_activation

    cfg = _load("case_presence_denied.json")
    d = decide_activation(cfg["system"], cfg["presence_decision"])
    assert d.allowed is False
    assert "presence" in d.reason.lower()


def test_presence_mode_silent_denies():
    from src.activation.state import decide_activation

    cfg = _load("case_presence_mode_silent.json")
    d = decide_activation(cfg["system"], cfg["presence_decision"])
    assert d.allowed is False
    assert "silent" in d.reason.lower()
