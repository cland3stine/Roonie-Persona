from __future__ import annotations

import json
from pathlib import Path

import pytest


def _load_fixture(name: str) -> dict:
    p = Path("tests/fixtures/v1_10e_provider_abstraction") / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_all_providers_disabled_defaults_to_none_and_is_silent():
    from providers.registry import ProviderRegistry

    cfg = _load_fixture("providers_all_disabled.json")
    reg = ProviderRegistry.from_dict(cfg)

    # Hard invariant: when default provider is "none", nothing can execute.
    prov = reg.get_default()
    assert prov.name == "none"
    assert prov.enabled is False

    # "Silent by default": calling generate must return None (no output).
    out = prov.generate(prompt="hello", context={})
    assert out is None


def test_one_provider_enabled_openai_registry_selects_it():
    from providers.registry import ProviderRegistry

    cfg = _load_fixture("providers_one_enabled_openai.json")
    reg = ProviderRegistry.from_dict(cfg)

    prov = reg.get_default()
    assert prov.name == "openai"
    assert prov.enabled is True

    # Stub provider: deterministic placeholder output (not real model call).
    out = prov.generate(prompt="ping", context={})
    assert out == "[openai stub] ping"


def test_invalid_default_provider_rejected():
    from providers.registry import ProviderRegistry

    cfg = _load_fixture("providers_invalid_default.json")

    with pytest.raises(ValueError) as e:
        ProviderRegistry.from_dict(cfg)

    assert "default_provider" in str(e.value).lower()


def test_live_mode_can_select_real_openai_provider_without_stub(monkeypatch):
    from providers.router import _mk_openai_provider, _provider_for_name

    monkeypatch.setenv("ROONIE_ENABLE_LIVE_PROVIDER_NETWORK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "providers.openai_real.OpenAIProvider.generate",
        lambda self, *, prompt, context: "real-openai-output",
    )

    provider = _provider_for_name(
        "openai",
        _mk_openai_provider(enabled=True),
        context={"allow_live_provider_network": True},
    )
    out = provider.generate(prompt="hello", context={})
    assert out == "real-openai-output"
