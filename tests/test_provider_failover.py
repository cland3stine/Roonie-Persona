"""Tests for cross-provider failover, circuit breaker, and stub responses (DEC-047 / BL-REL-001)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from providers.base import Provider
from providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(name: str, *, generate_return=None, generate_side_effect=None) -> Provider:
    """Create a mock Provider with configurable generate() behavior."""
    p = MagicMock(spec=Provider)
    p.name = name
    p.enabled = True
    if generate_side_effect is not None:
        p.generate.side_effect = generate_side_effect
    else:
        p.generate.return_value = generate_return
    return p


def _make_registry(default_provider: Provider) -> ProviderRegistry:
    reg = MagicMock(spec=ProviderRegistry)
    reg.get_default.return_value = default_provider
    return reg


def _routing_cfg(enabled=True, weights=None):
    return {
        "enabled": enabled,
        "general_route_mode": "weighted_random",
        "provider_weights": weights or {"grok": 50, "openai": 25, "anthropic": 25},
        "default_provider": "grok",
        "music_route_provider": "grok",
        "manual_override": "default",
    }


def _context(*, use_provider_config=True, direct_address=False, is_direct_mention=False):
    ctx = {
        "use_provider_config": use_provider_config,
        "message_text": "hello",
        "category": "GREETING",
        "session_id": "test-session",
        "event_id": "test-event-001",
        "direct_address": direct_address,
        "is_direct_mention": is_direct_mention,
    }
    return ctx


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Unit tests for the circuit breaker logic."""

    def setup_method(self):
        """Reset circuit breaker state before each test."""
        from providers import router
        with router._CIRCUIT_LOCK:
            router._CIRCUIT_STATE.clear()

    def test_circuit_stays_closed_under_threshold(self):
        from providers.router import _record_circuit_failure, _is_circuit_open, _CB_FAILURE_THRESHOLD

        for _ in range(_CB_FAILURE_THRESHOLD - 1):
            _record_circuit_failure("grok")
        assert not _is_circuit_open("grok"), "Circuit should remain closed below threshold"

    def test_circuit_opens_at_threshold(self):
        from providers.router import _record_circuit_failure, _is_circuit_open, _CB_FAILURE_THRESHOLD

        for _ in range(_CB_FAILURE_THRESHOLD):
            _record_circuit_failure("grok")
        assert _is_circuit_open("grok"), "Circuit should open at threshold"

    def test_circuit_recovers_after_timeout(self):
        from providers.router import (
            _record_circuit_failure,
            _is_circuit_open,
            _CB_FAILURE_THRESHOLD,
            _CB_RECOVERY_SECONDS,
            _CIRCUIT_STATE,
            _CIRCUIT_LOCK,
        )

        for _ in range(_CB_FAILURE_THRESHOLD):
            _record_circuit_failure("grok")
        assert _is_circuit_open("grok")

        # Simulate time passing by backdating opened_at
        with _CIRCUIT_LOCK:
            _CIRCUIT_STATE["grok"]["opened_at"] = time.monotonic() - _CB_RECOVERY_SECONDS - 1

        assert not _is_circuit_open("grok"), "Circuit should recover (half-open) after timeout"

    def test_success_resets_circuit(self):
        from providers.router import (
            _record_circuit_failure,
            _record_circuit_success,
            _is_circuit_open,
            _CB_FAILURE_THRESHOLD,
        )

        for _ in range(_CB_FAILURE_THRESHOLD):
            _record_circuit_failure("grok")
        assert _is_circuit_open("grok")

        _record_circuit_success("grok")
        assert not _is_circuit_open("grok"), "Success should reset circuit to closed"

    def test_unknown_provider_circuit_is_closed(self):
        from providers.router import _is_circuit_open

        assert not _is_circuit_open("nonexistent_provider")


# ---------------------------------------------------------------------------
# Failover Chain Tests
# ---------------------------------------------------------------------------


class TestFailoverChain:
    """Unit tests for _failover_chain()."""

    def setup_method(self):
        from providers import router
        with router._CIRCUIT_LOCK:
            router._CIRCUIT_STATE.clear()

    def test_primary_fails_fallback_succeeds(self):
        from providers.router import _failover_chain

        primary = _make_provider("grok", generate_return=None)
        ctx = _context()

        with patch("providers.router._provider_for_name") as mock_pfn:
            fallback = _make_provider("openai", generate_return="fallback response")
            mock_pfn.return_value = fallback

            result, name = _failover_chain(
                failed_provider="grok",
                approved_providers={"grok", "openai", "anthropic"},
                routing_runtime_cfg=_routing_cfg(),
                prompt="test prompt",
                context=ctx,
                primary_provider=primary,
            )

        assert result == "fallback response"
        assert name in {"openai", "anthropic"}  # Both have weight 25, first tried wins

    def test_all_providers_fail_returns_none(self):
        from providers.router import _failover_chain

        primary = _make_provider("grok", generate_return=None)
        ctx = _context()

        with patch("providers.router._provider_for_name") as mock_pfn:
            mock_pfn.return_value = _make_provider("openai", generate_return=None)

            result, name = _failover_chain(
                failed_provider="grok",
                approved_providers={"grok", "openai", "anthropic"},
                routing_runtime_cfg=_routing_cfg(),
                prompt="test prompt",
                context=ctx,
                primary_provider=primary,
            )

        assert result is None
        assert name is None

    def test_skips_circuit_open_providers(self):
        from providers.router import _failover_chain, _record_circuit_failure, _CB_FAILURE_THRESHOLD

        # Open circuit for openai
        for _ in range(_CB_FAILURE_THRESHOLD):
            _record_circuit_failure("openai")

        primary = _make_provider("grok", generate_return=None)
        ctx = _context()
        call_names = []

        def track_provider_for_name(name, *args, **kwargs):
            call_names.append(name)
            return _make_provider(name, generate_return="from " + name)

        with patch("providers.router._provider_for_name", side_effect=track_provider_for_name):
            result, name = _failover_chain(
                failed_provider="grok",
                approved_providers={"grok", "openai", "anthropic"},
                routing_runtime_cfg=_routing_cfg(),
                prompt="test prompt",
                context=ctx,
                primary_provider=primary,
            )

        # openai should be skipped due to open circuit; anthropic should be tried
        assert "openai" not in call_names
        assert result is not None
        assert name == "anthropic"

    def test_failover_sets_context_fields(self):
        from providers.router import _failover_chain

        primary = _make_provider("grok", generate_return=None)
        ctx = _context()

        with patch("providers.router._provider_for_name") as mock_pfn:
            mock_pfn.return_value = _make_provider("openai", generate_return="ok")

            result, name = _failover_chain(
                failed_provider="grok",
                approved_providers={"grok", "openai", "anthropic"},
                routing_runtime_cfg=_routing_cfg(),
                prompt="test prompt",
                context=ctx,
                primary_provider=primary,
            )

        assert result == "ok"
        assert "failover_providers_tried" in ctx
        assert "grok" in ctx["failover_providers_tried"]

    def test_fallback_ordered_by_weight_descending(self):
        from providers.router import _failover_chain

        primary = _make_provider("anthropic", generate_return=None)
        ctx = _context()
        call_order = []

        def track_pfn(name, *args, **kwargs):
            call_order.append(name)
            # First candidate fails, second succeeds
            if name == "grok":
                return _make_provider(name, generate_return=None)
            return _make_provider(name, generate_return="from " + name)

        with patch("providers.router._provider_for_name", side_effect=track_pfn):
            result, name = _failover_chain(
                failed_provider="anthropic",
                approved_providers={"grok", "openai", "anthropic"},
                routing_runtime_cfg=_routing_cfg(weights={"grok": 50, "openai": 25, "anthropic": 25}),
                prompt="test prompt",
                context=ctx,
                primary_provider=primary,
            )

        # grok (50) should be tried before openai (25)
        assert call_order[0] == "grok"
        assert result == "from openai"
        assert name == "openai"


# ---------------------------------------------------------------------------
# Stub Response Tests
# ---------------------------------------------------------------------------


class TestStubResponses:
    """Unit tests for _maybe_stub_response()."""

    def setup_method(self):
        import providers.router as r
        r._STUB_LAST_SENT = 0.0

    def test_stub_for_direct_address(self):
        from providers.router import _maybe_stub_response

        ctx = {"direct_address": True, "is_direct_mention": True}
        result = _maybe_stub_response(ctx)
        assert result is not None
        assert ctx.get("stub_response") is True

    def test_no_stub_for_non_direct(self):
        from providers.router import _maybe_stub_response

        ctx = {"direct_address": False, "is_direct_mention": False}
        result = _maybe_stub_response(ctx)
        assert result is None

    def test_stub_rate_limited(self):
        from providers.router import _maybe_stub_response
        import providers.router as r

        # First call should succeed
        ctx1 = {"direct_address": True}
        result1 = _maybe_stub_response(ctx1)
        assert result1 is not None

        # Second call within cooldown should be rate limited
        ctx2 = {"direct_address": True}
        result2 = _maybe_stub_response(ctx2)
        assert result2 is None

    def test_stub_clears_suppression_reason(self):
        from providers.router import _maybe_stub_response

        ctx = {"direct_address": True, "suppression_reason": "PROVIDER_ERROR"}
        result = _maybe_stub_response(ctx)
        assert result is not None
        assert ctx["suppression_reason"] is None


# ---------------------------------------------------------------------------
# Integration Tests (route_generate with failover)
# ---------------------------------------------------------------------------


class TestRouteGenerateFailover:
    """Integration tests for failover in route_generate()."""

    def setup_method(self):
        import providers.router as r
        with r._CIRCUIT_LOCK:
            r._CIRCUIT_STATE.clear()
        r._STUB_LAST_SENT = 0.0

    @patch("providers.router.get_resolved_model_config")
    @patch("providers.router.get_provider_runtime_status")
    @patch("providers.router.get_routing_runtime_status")
    @patch("providers.router._real_provider_network_enabled", return_value=False)
    def test_primary_none_triggers_failover(self, _net, mock_routing, mock_status, mock_model):
        from providers.router import route_generate

        mock_model.return_value = {
            "provider_models": {"grok": "grok-test", "openai": "gpt-test", "anthropic": "claude-test"},
            "openai_model": "gpt-test",
            "director_model": "gpt-test",
            "grok_model": "grok-test",
        }
        mock_status.return_value = {
            "active_provider": "grok",
            "approved_providers": ["grok", "openai", "anthropic"],
            "caps": {},
            "usage": {},
        }
        mock_routing.return_value = _routing_cfg()

        # Primary returns None (the core bug)
        primary = _make_provider("grok", generate_return=None)
        registry = _make_registry(primary)

        # Patch _provider_for_name to return failing primary and successful fallback
        call_count = {"n": 0}

        def patched_pfn(name, registry_default, *, context=None):
            call_count["n"] += 1
            if name == "grok":
                return _make_provider("grok", generate_return=None)
            return _make_provider(name, generate_return=f"fallback from {name}")

        with patch("providers.router._provider_for_name", side_effect=patched_pfn):
            ctx = _context(use_provider_config=True)
            result = route_generate(
                registry=registry,
                routing_cfg={},
                prompt="test",
                context=ctx,
            )

        assert result is not None
        assert "fallback" in result
        assert ctx.get("failover_used") is True

    @patch("providers.router.get_resolved_model_config")
    @patch("providers.router.get_provider_runtime_status")
    @patch("providers.router.get_routing_runtime_status")
    @patch("providers.router._real_provider_network_enabled", return_value=False)
    def test_primary_exception_triggers_failover(self, _net, mock_routing, mock_status, mock_model):
        from providers.router import route_generate

        mock_model.return_value = {
            "provider_models": {"grok": "grok-test", "openai": "gpt-test", "anthropic": "claude-test"},
            "openai_model": "gpt-test",
            "director_model": "gpt-test",
            "grok_model": "grok-test",
        }
        mock_status.return_value = {
            "active_provider": "grok",
            "approved_providers": ["grok", "openai", "anthropic"],
            "caps": {},
            "usage": {},
        }
        mock_routing.return_value = _routing_cfg()

        # Primary throws exception
        primary = _make_provider("grok", generate_side_effect=ConnectionError("timeout"))
        registry = _make_registry(primary)

        def patched_pfn(name, registry_default, *, context=None):
            if name == "grok":
                return _make_provider("grok", generate_side_effect=ConnectionError("timeout"))
            return _make_provider(name, generate_return=f"fallback from {name}")

        with patch("providers.router._provider_for_name", side_effect=patched_pfn):
            ctx = _context(use_provider_config=True)
            result = route_generate(
                registry=registry,
                routing_cfg={},
                prompt="test",
                context=ctx,
            )

        assert result is not None
        assert "fallback" in result
        assert ctx.get("failover_used") is True

    @patch("providers.router.get_resolved_model_config")
    @patch("providers.router.get_provider_runtime_status")
    @patch("providers.router.get_routing_runtime_status")
    @patch("providers.router._real_provider_network_enabled", return_value=False)
    def test_successful_primary_no_failover(self, _net, mock_routing, mock_status, mock_model):
        from providers.router import route_generate

        mock_model.return_value = {
            "provider_models": {"grok": "grok-test", "openai": "gpt-test", "anthropic": "claude-test"},
            "openai_model": "gpt-test",
            "director_model": "gpt-test",
            "grok_model": "grok-test",
        }
        mock_status.return_value = {
            "active_provider": "grok",
            "approved_providers": ["grok", "openai", "anthropic"],
            "caps": {},
            "usage": {},
        }
        mock_routing.return_value = _routing_cfg()

        primary = _make_provider("grok", generate_return="primary success")
        registry = _make_registry(primary)

        def patched_pfn(name, registry_default, *, context=None):
            return _make_provider(name, generate_return="primary success")

        with patch("providers.router._provider_for_name", side_effect=patched_pfn):
            ctx = _context(use_provider_config=True)
            result = route_generate(
                registry=registry,
                routing_cfg={},
                prompt="test",
                context=ctx,
            )

        assert result == "primary success"
        assert ctx.get("failover_used") is None

    @patch("providers.router.get_resolved_model_config")
    @patch("providers.router.get_provider_runtime_status")
    @patch("providers.router.get_routing_runtime_status")
    @patch("providers.router._real_provider_network_enabled", return_value=False)
    def test_all_fail_stub_for_direct_address(self, _net, mock_routing, mock_status, mock_model):
        from providers.router import route_generate

        mock_model.return_value = {
            "provider_models": {"grok": "grok-test", "openai": "gpt-test", "anthropic": "claude-test"},
            "openai_model": "gpt-test",
            "director_model": "gpt-test",
            "grok_model": "grok-test",
        }
        mock_status.return_value = {
            "active_provider": "grok",
            "approved_providers": ["grok", "openai", "anthropic"],
            "caps": {},
            "usage": {},
        }
        mock_routing.return_value = _routing_cfg()

        # All providers return None
        primary = _make_provider("grok", generate_return=None)
        registry = _make_registry(primary)

        def patched_pfn(name, registry_default, *, context=None):
            return _make_provider(name, generate_return=None)

        with patch("providers.router._provider_for_name", side_effect=patched_pfn):
            ctx = _context(use_provider_config=True, direct_address=True, is_direct_mention=True)
            result = route_generate(
                registry=registry,
                routing_cfg={},
                prompt="test",
                context=ctx,
            )

        assert result is not None  # Stub should fire for direct-address
        assert ctx.get("stub_response") is True
        assert ctx.get("active_provider") == "stub"

    @patch("providers.router.get_resolved_model_config")
    @patch("providers.router.get_provider_runtime_status")
    @patch("providers.router.get_routing_runtime_status")
    @patch("providers.router._real_provider_network_enabled", return_value=False)
    def test_all_fail_noop_for_non_direct(self, _net, mock_routing, mock_status, mock_model):
        from providers.router import route_generate

        mock_model.return_value = {
            "provider_models": {"grok": "grok-test", "openai": "gpt-test", "anthropic": "claude-test"},
            "openai_model": "gpt-test",
            "director_model": "gpt-test",
            "grok_model": "grok-test",
        }
        mock_status.return_value = {
            "active_provider": "grok",
            "approved_providers": ["grok", "openai", "anthropic"],
            "caps": {},
            "usage": {},
        }
        mock_routing.return_value = _routing_cfg()

        primary = _make_provider("grok", generate_return=None)
        registry = _make_registry(primary)

        def patched_pfn(name, registry_default, *, context=None):
            return _make_provider(name, generate_return=None)

        with patch("providers.router._provider_for_name", side_effect=patched_pfn):
            ctx = _context(use_provider_config=True, direct_address=False)
            result = route_generate(
                registry=registry,
                routing_cfg={},
                prompt="test",
                context=ctx,
            )

        assert result is None  # No stub for non-direct
        assert ctx.get("suppression_reason") == "PROVIDER_ERROR"


# ---------------------------------------------------------------------------
# Circuit Breaker Status Tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerStatus:
    """Tests for get_circuit_breaker_status() dashboard API."""

    def setup_method(self):
        from providers import router
        with router._CIRCUIT_LOCK:
            router._CIRCUIT_STATE.clear()

    def test_empty_status(self):
        from providers.router import get_circuit_breaker_status

        status = get_circuit_breaker_status()
        assert status == {}

    def test_status_shows_open_circuit(self):
        from providers.router import get_circuit_breaker_status, _record_circuit_failure, _CB_FAILURE_THRESHOLD

        for _ in range(_CB_FAILURE_THRESHOLD):
            _record_circuit_failure("grok")

        status = get_circuit_breaker_status()
        assert "grok" in status
        assert status["grok"]["is_open"] is True
        assert status["grok"]["failures"] >= _CB_FAILURE_THRESHOLD
