"""Tests for weighted provider routing and real OpenAI Moderation API."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Weighted routing tests
# ---------------------------------------------------------------------------


class TestSelectProviderWeighted:
    """Unit tests for _select_provider_weighted helper."""

    def test_deterministic_same_seed_same_provider(self):
        from providers.router import _select_provider_weighted

        digest = hashlib.sha256(b"stable-seed").digest()
        results = {_select_provider_weighted(["grok", "openai", "anthropic"], {"grok": 50, "openai": 25, "anthropic": 25}, digest) for _ in range(20)}
        assert len(results) == 1, "Same seed must always yield the same provider"

    def test_distribution_approximates_weights(self):
        from providers.router import _select_provider_weighted

        weights = {"grok": 50, "openai": 25, "anthropic": 25}
        candidates = ["grok", "openai", "anthropic"]
        counts: Counter = Counter()
        n = 10_000
        for i in range(n):
            digest = hashlib.sha256(f"msg-{i}".encode()).digest()
            counts[_select_provider_weighted(candidates, weights, digest)] += 1
        grok_pct = counts["grok"] / n
        openai_pct = counts["openai"] / n
        anthropic_pct = counts["anthropic"] / n
        assert 0.45 <= grok_pct <= 0.55, f"Grok expected ~50%, got {grok_pct:.1%}"
        assert 0.20 <= openai_pct <= 0.30, f"OpenAI expected ~25%, got {openai_pct:.1%}"
        assert 0.20 <= anthropic_pct <= 0.30, f"Anthropic expected ~25%, got {anthropic_pct:.1%}"

    def test_zero_weight_provider_excluded(self):
        from providers.router import _select_provider_weighted

        weights = {"grok": 50, "openai": 0, "anthropic": 50}
        candidates = ["grok", "openai", "anthropic"]
        for i in range(500):
            digest = hashlib.sha256(f"zero-{i}".encode()).digest()
            result = _select_provider_weighted(candidates, weights, digest)
            assert result != "openai", "Zero-weight provider must never be selected"

    def test_single_candidate_shortcut(self):
        from providers.router import _select_provider_weighted

        digest = hashlib.sha256(b"single").digest()
        assert _select_provider_weighted(["grok"], {"grok": 50}, digest) == "grok"

    def test_empty_candidates_fallback(self):
        from providers.router import _select_provider_weighted

        digest = hashlib.sha256(b"empty").digest()
        assert _select_provider_weighted([], {}, digest) == "openai"

    def test_all_zero_weights_uniform_fallback(self):
        from providers.router import _select_provider_weighted

        candidates = ["grok", "openai", "anthropic"]
        weights = {"grok": 0, "openai": 0, "anthropic": 0}
        results = set()
        for i in range(200):
            digest = hashlib.sha256(f"allzero-{i}".encode()).digest()
            results.add(_select_provider_weighted(candidates, weights, digest))
        # With uniform fallback, at least 2 providers should appear
        assert len(results) >= 2


class TestWeightedRandomRouting:
    """Integration tests for weighted_random mode in _select_provider_from_routing."""

    def test_weighted_random_selects_from_approved(self, tmp_path):
        from providers.router import _select_provider_from_routing

        routing_cfg = {
            "enabled": True,
            "manual_override": "default",
            "general_route_mode": "weighted_random",
            "music_route_provider": "grok",
            "provider_weights": {"grok": 50, "openai": 25, "anthropic": 25},
        }
        approved = {"grok", "openai", "anthropic"}
        context = {"provider_roulette_seed": "test-seed-123"}
        result = _select_provider_from_routing(
            routing_cfg=routing_cfg,
            fallback_provider="openai",
            routing_class="general",
            approved_providers=approved,
            context=context,
        )
        assert result in approved
        assert context.get("general_route_mode") == "weighted_random"
        assert "provider_weights" in context

    def test_weighted_random_respects_approved_filter(self):
        from providers.router import _select_provider_from_routing

        routing_cfg = {
            "enabled": True,
            "manual_override": "default",
            "general_route_mode": "weighted_random",
            "music_route_provider": "grok",
            "provider_weights": {"grok": 50, "openai": 25, "anthropic": 25},
        }
        # Only openai+anthropic approved — grok should never appear
        approved = {"openai", "anthropic"}
        for i in range(200):
            ctx = {"provider_roulette_seed": f"filter-{i}"}
            result = _select_provider_from_routing(
                routing_cfg=routing_cfg,
                fallback_provider="openai",
                routing_class="general",
                approved_providers=approved,
                context=ctx,
            )
            assert result in approved, f"Got {result} which is not in approved set"

    def test_music_route_unaffected_by_weighted_mode(self):
        from providers.router import _select_provider_from_routing

        routing_cfg = {
            "enabled": True,
            "manual_override": "default",
            "general_route_mode": "weighted_random",
            "music_route_provider": "grok",
            "provider_weights": {"grok": 0, "openai": 50, "anthropic": 50},
        }
        result = _select_provider_from_routing(
            routing_cfg=routing_cfg,
            fallback_provider="openai",
            routing_class="music_culture",
            approved_providers={"grok", "openai", "anthropic"},
            context={},
        )
        assert result == "grok", "Music route always goes to grok regardless of weights"

    def test_custom_weights_honored(self):
        from providers.router import _select_provider_from_routing

        # Give 100% weight to anthropic only
        routing_cfg = {
            "enabled": True,
            "manual_override": "default",
            "general_route_mode": "weighted_random",
            "music_route_provider": "grok",
            "provider_weights": {"grok": 0, "openai": 0, "anthropic": 100},
        }
        approved = {"grok", "openai", "anthropic"}
        for i in range(100):
            ctx = {"provider_roulette_seed": f"custom-{i}"}
            result = _select_provider_from_routing(
                routing_cfg=routing_cfg,
                fallback_provider="openai",
                routing_class="general",
                approved_providers=approved,
                context=ctx,
            )
            assert result == "anthropic", f"100% weight to anthropic but got {result}"


class TestWeightedRoutingConfig:
    """Tests for config schema: normalization, defaults, update."""

    def test_default_routing_config_has_provider_weights(self):
        from providers.router import _default_routing_config

        cfg = _default_routing_config()
        assert "provider_weights" in cfg
        assert cfg["provider_weights"] == {"grok": 50, "openai": 25, "anthropic": 25}

    def test_normalize_fills_missing_weights(self):
        from providers.router import _normalize_routing_config

        raw = {"enabled": True, "general_route_mode": "weighted_random"}
        cfg = _normalize_routing_config(raw)
        assert cfg["provider_weights"]["grok"] == 50
        assert cfg["provider_weights"]["openai"] == 25
        assert cfg["provider_weights"]["anthropic"] == 25

    def test_normalize_resets_all_zero_weights(self):
        from providers.router import _normalize_routing_config

        raw = {"provider_weights": {"grok": 0, "openai": 0, "anthropic": 0}}
        cfg = _normalize_routing_config(raw)
        assert cfg["provider_weights"] == {"grok": 50, "openai": 25, "anthropic": 25}

    def test_normalize_preserves_custom_weights(self):
        from providers.router import _normalize_routing_config

        raw = {"provider_weights": {"grok": 10, "openai": 80, "anthropic": 10}}
        cfg = _normalize_routing_config(raw)
        assert cfg["provider_weights"] == {"grok": 10, "openai": 80, "anthropic": 10}

    def test_normalize_clamps_negative_weights(self):
        from providers.router import _normalize_routing_config

        raw = {"provider_weights": {"grok": -5, "openai": 25, "anthropic": 25}}
        cfg = _normalize_routing_config(raw)
        assert cfg["provider_weights"]["grok"] == 0

    def test_weighted_random_in_general_route_modes(self):
        from providers.router import _GENERAL_ROUTE_MODES

        assert "weighted_random" in _GENERAL_ROUTE_MODES

    def test_update_routing_controls_accepts_provider_weights(self, tmp_path):
        from providers.router import (
            _normalize_routing_config,
            _write_json_atomic,
            update_routing_runtime_controls,
        )

        cfg_path = tmp_path / "routing_config.json"
        _write_json_atomic(cfg_path, _normalize_routing_config({}))
        with patch("providers.router._routing_config_path", return_value=cfg_path):
            old, new = update_routing_runtime_controls({
                "provider_weights": {"grok": 70, "openai": 20, "anthropic": 10},
            })
        assert new["provider_weights"]["grok"] == 70
        assert new["provider_weights"]["openai"] == 20
        assert new["provider_weights"]["anthropic"] == 10

    def test_update_routing_controls_rejects_non_dict_weights(self, tmp_path):
        from providers.router import (
            _normalize_routing_config,
            _write_json_atomic,
            update_routing_runtime_controls,
        )

        cfg_path = tmp_path / "routing_config.json"
        _write_json_atomic(cfg_path, _normalize_routing_config({}))
        with patch("providers.router._routing_config_path", return_value=cfg_path):
            with pytest.raises(ValueError, match="provider_weights must be an object"):
                update_routing_runtime_controls({"provider_weights": "invalid"})


# ---------------------------------------------------------------------------
# OpenAI Moderation API tests
# ---------------------------------------------------------------------------


def _mock_moderation_response(flagged: bool, categories: dict | None = None, scores: dict | None = None) -> bytes:
    """Build a fake OpenAI moderation API response body."""
    if categories is None:
        categories = {}
    if scores is None:
        scores = {}
    return json.dumps({
        "id": "modr-test",
        "model": "omni-moderation-latest",
        "results": [{
            "flagged": flagged,
            "categories": categories,
            "category_scores": scores,
        }],
    }).encode("utf-8")


class TestModerationAPI:
    """Tests for _call_openai_moderation_api and _moderation_allows_model_output."""

    def test_flagged_response_blocks(self):
        from providers.router import _moderation_allows_model_output

        mock_resp = MagicMock()
        mock_resp.read.return_value = _mock_moderation_response(
            flagged=True,
            categories={"harassment": True},
            scores={"harassment": 0.95},
        )
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        context: dict = {}
        with patch("providers.router.urllib.request.urlopen", return_value=mock_resp), \
             patch("providers.router._resolve_secret_or_env", return_value="sk-test"):
            result = _moderation_allows_model_output(
                text="some harmful text",
                context=context,
                test_overrides=None,
            )
        assert result is False
        assert "moderation_flagged_categories" in context
        assert context["moderation_flagged_categories"] == {"harassment": True}

    def test_clean_response_allows(self):
        from providers.router import _moderation_allows_model_output

        mock_resp = MagicMock()
        mock_resp.read.return_value = _mock_moderation_response(flagged=False)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        context: dict = {}
        with patch("providers.router.urllib.request.urlopen", return_value=mock_resp), \
             patch("providers.router._resolve_secret_or_env", return_value="sk-test"):
            result = _moderation_allows_model_output(
                text="hello friend",
                context=context,
                test_overrides=None,
            )
        assert result is True
        assert "moderation_flagged_categories" not in context

    def test_api_failure_allows_with_error_flag(self):
        from providers.router import _moderation_allows_model_output

        context: dict = {}
        with patch("providers.router.urllib.request.urlopen", side_effect=OSError("network error")), \
             patch("providers.router._resolve_secret_or_env", return_value="sk-test"):
            result = _moderation_allows_model_output(
                text="some text",
                context=context,
                test_overrides=None,
            )
        assert result is True
        assert context.get("moderation_api_error") is True

    def test_missing_api_key_allows_with_error_flag(self):
        from providers.router import _moderation_allows_model_output

        context: dict = {}
        with patch("providers.router._resolve_secret_or_env", return_value=""):
            result = _moderation_allows_model_output(
                text="some text",
                context=context,
                test_overrides=None,
            )
        assert result is True
        assert context.get("moderation_api_error") is True

    def test_test_override_block_shortcircuits(self):
        from providers.router import _moderation_allows_model_output

        context: dict = {}
        result = _moderation_allows_model_output(
            text="anything",
            context=context,
            test_overrides={"moderation_behavior": "block"},
        )
        assert result is False

    def test_test_override_allow_shortcircuits(self):
        from providers.router import _moderation_allows_model_output

        context: dict = {}
        result = _moderation_allows_model_output(
            text="anything",
            context=context,
            test_overrides={"moderation_behavior": "allow"},
        )
        assert result is True

    def test_force_moderation_context_block(self):
        from providers.router import _moderation_allows_model_output

        result = _moderation_allows_model_output(
            text="anything",
            context={"force_moderation_result": "block"},
            test_overrides=None,
        )
        assert result is False

    def test_force_moderation_context_allow(self):
        from providers.router import _moderation_allows_model_output

        result = _moderation_allows_model_output(
            text="anything",
            context={"force_moderation_result": "allow"},
            test_overrides=None,
        )
        assert result is True

    def test_empty_text_allows_without_api_call(self):
        from providers.router import _moderation_allows_model_output

        context: dict = {}
        # No mocking needed — empty text should short-circuit before API call
        result = _moderation_allows_model_output(
            text="",
            context=context,
            test_overrides=None,
        )
        assert result is True
        assert "moderation_api_error" not in context

    def test_whitespace_only_text_allows_without_api_call(self):
        from providers.router import _moderation_allows_model_output

        context: dict = {}
        result = _moderation_allows_model_output(
            text="   ",
            context=context,
            test_overrides=None,
        )
        assert result is True
        assert "moderation_api_error" not in context
