from __future__ import annotations

import json
from pathlib import Path

import pytest


def _load(name: str) -> dict:
    p = Path("tests/fixtures/v1_10f_routing_validation") / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_default_none_is_silent():
    from src.providers.registry import ProviderRegistry
    from src.providers.router import route_generate

    cfg = _load("case_default_none.json")
    reg = ProviderRegistry.from_dict(cfg)

    out = route_generate(
        registry=reg,
        routing_cfg=cfg.get("routing", {}),
        prompt="hello",
        context={},
    )
    assert out is None


def test_primary_only_returns_primary_output():
    from src.providers.registry import ProviderRegistry
    from src.providers.router import route_generate

    cfg = _load("case_primary_only.json")
    reg = ProviderRegistry.from_dict(cfg)

    out = route_generate(
        registry=reg,
        routing_cfg=cfg.get("routing", {}),
        prompt="ping",
        context={},
    )
    assert out == "[openai stub] ping"


def test_shadow_enabled_executes_shadow_but_returns_primary():
    from src.providers.registry import ProviderRegistry
    from src.providers.router import route_generate

    cfg = _load("case_shadow_enabled.json")
    reg = ProviderRegistry.from_dict(cfg)

    out = route_generate(
        registry=reg,
        routing_cfg=cfg.get("routing", {}),
        prompt="ping",
        context={},
    )
    # Shadow never changes returned output
    assert out == "[openai stub] ping"


def test_shadow_disabled_provider_is_contained():
    from src.providers.registry import ProviderRegistry
    from src.providers.router import route_generate

    cfg = _load("case_shadow_disabled_provider.json")
    reg = ProviderRegistry.from_dict(cfg)

    out = route_generate(
        registry=reg,
        routing_cfg=cfg.get("routing", {}),
        prompt="ping",
        context={},
    )
    # Must not crash; primary still returns
    assert out == "[openai stub] ping"


def test_primary_throw_is_contained_and_returns_none():
    from src.providers.registry import ProviderRegistry
    from src.providers.router import route_generate

    cfg = _load("case_primary_throws.json")
    reg = ProviderRegistry.from_dict(cfg)

    out = route_generate(
        registry=reg,
        routing_cfg=cfg.get("routing", {}),
        prompt="ping",
        context={},
        test_overrides=cfg.get("test_overrides"),
    )
    # Primary failure must be contained; no output returned
    assert out is None
