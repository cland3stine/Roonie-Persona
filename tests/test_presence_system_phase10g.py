from __future__ import annotations

import json
from pathlib import Path


def _load(name: str) -> dict:
    p = Path("tests/fixtures/v1_10g_presence") / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_presence_budget_exhausted_denies():
    from presence.policy import decide_presence

    cfg = _load("case_budget_exhausted.json")
    d = decide_presence(cfg)
    assert d.allowed is False
    assert "budget" in d.reason.lower()


def test_presence_cooldown_active_denies():
    from presence.policy import decide_presence

    cfg = _load("case_cooldown_active.json")
    d = decide_presence(cfg)
    assert d.allowed is False
    assert "cooldown" in d.reason.lower()


def test_presence_allowed_ambient_allows():
    from presence.policy import decide_presence

    cfg = _load("case_allowed_ambient.json")
    d = decide_presence(cfg)
    assert d.allowed is True
    assert d.lane == "ambient"


def test_named_budget_exhausted_denies():
    from presence.policy import decide_presence

    cfg = _load("case_named_budget_exhausted.json")
    d = decide_presence(cfg)
    assert d.allowed is False
    assert "named" in d.reason.lower()


def test_named_allowed_allows():
    from presence.policy import decide_presence

    cfg = _load("case_named_allowed.json")
    d = decide_presence(cfg)
    assert d.allowed is True
    assert d.lane == "named"
