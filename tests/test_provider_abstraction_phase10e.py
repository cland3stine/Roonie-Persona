from __future__ import annotations

import json
import os
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


def test_model_resolution_prefers_explicit_env_values(monkeypatch):
    from providers.router import get_resolved_model_config

    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ROONIE_DIRECTOR_MODEL", "gpt-5.2")
    monkeypatch.setenv("GROK_MODEL", "grok-4-1-fast-reasoning")

    cfg = get_resolved_model_config()
    assert cfg["openai_model"] == "gpt-5.2"
    assert cfg["director_model"] == "gpt-5.2"
    assert cfg["grok_model"] == "grok-4-1-fast-reasoning"
    assert cfg["provider_models"]["openai"] == "gpt-5.2"
    assert cfg["provider_models"]["grok"] == "grok-4-1-fast-reasoning"
    assert "OPENAI_MODEL" not in cfg["fallback_defaults"]
    assert "ROONIE_DIRECTOR_MODEL" not in cfg["fallback_defaults"]
    assert "GROK_MODEL" not in cfg["fallback_defaults"]


def test_model_resolution_can_load_from_secrets_and_seed_process_env(monkeypatch):
    import providers.router as router

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("ROONIE_DIRECTOR_MODEL", raising=False)
    monkeypatch.delenv("GROK_MODEL", raising=False)
    monkeypatch.setattr(
        router,
        "_read_secrets_env",
        lambda: {
            "OPENAI_MODEL": "gpt-5.2",
            "ROONIE_DIRECTOR_MODEL": "gpt-5.2",
            "GROK_MODEL": "grok-4-1-fast-reasoning",
        },
    )

    cfg = router.get_resolved_model_config(ensure_env=True)
    assert cfg["openai_model"] == "gpt-5.2"
    assert cfg["director_model"] == "gpt-5.2"
    assert cfg["grok_model"] == "grok-4-1-fast-reasoning"
    assert os.getenv("OPENAI_MODEL") == "gpt-5.2"
    assert os.getenv("ROONIE_DIRECTOR_MODEL") == "gpt-5.2"
    assert os.getenv("GROK_MODEL") == "grok-4-1-fast-reasoning"


def test_llm_key_store_migrates_from_secrets_and_seeds_env(monkeypatch, tmp_path: Path):
    import providers.router as router

    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=openai-test-key",
                "GROK_API_KEY=grok-test-key",
                "ANTHROPIC_API_KEY=anthropic-test-key",
            ]
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROONIE_ENCRYPT_LLM_KEYS_AT_REST", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(router, "_LLM_KEY_STORE_CACHE", None)
    monkeypatch.setattr(router, "_LLM_KEY_STORE_CACHE_MTIME_NS", None)
    monkeypatch.setattr(router, "_LLM_KEY_STORE_CACHE_PATH", None)

    migrate = router.migrate_llm_key_store_from_secrets_env(path=secrets_path, overwrite_existing=False)
    assert migrate["migrated"] == 3
    assert migrate["wrote"] is True
    assert migrate["encryption"] == "plaintext"

    store_path = data_dir / "llm_key_store.json"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    keys = payload.get("keys", {})
    assert keys.get("OPENAI_API_KEY", {}).get("value") == "openai-test-key"
    assert keys.get("GROK_API_KEY", {}).get("value") == "grok-test-key"
    assert keys.get("ANTHROPIC_API_KEY", {}).get("value") == "anthropic-test-key"

    seeded = router.seed_process_env_from_llm_key_store(overwrite_existing=False)
    assert seeded["set"] >= 3
    assert os.getenv("OPENAI_API_KEY") == "openai-test-key"
    assert os.getenv("GROK_API_KEY") == "grok-test-key"
    assert os.getenv("ANTHROPIC_API_KEY") == "anthropic-test-key"


def test_api_key_resolution_prefers_env_then_store_then_secrets(monkeypatch, tmp_path: Path):
    import providers.router as router

    source_path = tmp_path / "secrets.env"
    source_path.write_text("OPENAI_API_KEY=store-key\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ROONIE_DASHBOARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ROONIE_ENCRYPT_LLM_KEYS_AT_REST", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(router, "_LLM_KEY_STORE_CACHE", None)
    monkeypatch.setattr(router, "_LLM_KEY_STORE_CACHE_MTIME_NS", None)
    monkeypatch.setattr(router, "_LLM_KEY_STORE_CACHE_PATH", None)
    router.migrate_llm_key_store_from_secrets_env(path=source_path, overwrite_existing=True)

    monkeypatch.setattr(router, "_read_secrets_env", lambda: {"OPENAI_API_KEY": "secrets-key"})
    assert router._resolve_secret_or_env("OPENAI_API_KEY") == "store-key"

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert router._resolve_secret_or_env("OPENAI_API_KEY") == "env-key"


def test_route_generate_uses_director_model_for_openai(monkeypatch, tmp_path: Path):
    from providers.registry import ProviderRegistry
    from providers.router import route_generate

    providers_path = tmp_path / "providers_config.json"
    routing_path = tmp_path / "routing_config.json"
    providers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_provider": "openai",
                "approved_providers": ["openai", "grok"],
                "caps": {"daily_requests_max": 100, "daily_tokens_max": 0, "hard_stop_on_cap": True},
                "usage": {"day": "2026-02-16", "requests": 0, "tokens": 0},
            }
        ),
        encoding="utf-8",
    )
    routing_path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": False,
                "default_provider": "openai",
                "music_route_provider": "grok",
                "moderation_provider": "openai",
                "manual_override": "default",
                "classification_rules": {"music_culture_keywords": ["track"], "artist_title_pattern": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROONIE_PROVIDERS_CONFIG_PATH", str(providers_path))
    monkeypatch.setenv("ROONIE_ROUTING_CONFIG_PATH", str(routing_path))
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ROONIE_DIRECTOR_MODEL", "gpt-5.2")

    registry = ProviderRegistry.from_dict(
        {
            "default_provider": "openai",
            "providers": {
                "openai": {"enabled": True},
                "grok": {"enabled": True},
                "anthropic": {"enabled": False},
            },
        }
    )
    context = {"use_provider_config": True, "message_text": "hello"}
    out = route_generate(registry=registry, routing_cfg={}, prompt="ping", context=context)
    assert out == "[openai stub] ping"
    assert context.get("model") == "gpt-5.2"
