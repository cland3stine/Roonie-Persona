from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from providers.base import Provider


@dataclass(frozen=True)
class ProviderRegistry:
    _default: Provider

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "ProviderRegistry":
        default_name = str(cfg.get("default_provider", "none")).strip().lower()
        providers_cfg = cfg.get("providers", {}) or {}

        # Build provider objects with deterministic stub behavior.
        def _mk(name: str) -> Provider:
            raw = providers_cfg.get(name, {}) or {}
            enabled = bool(raw.get("enabled", False))
            return Provider(name=name, enabled=enabled)

        if default_name == "none":
            return cls(_default=Provider(name="none", enabled=False))

        # Validate default exists and is enabled.
        prov = _mk(default_name)
        if prov.name not in providers_cfg:
            raise ValueError(f"default_provider '{default_name}' not present in providers")
        if not prov.enabled:
            raise ValueError(f"default_provider '{default_name}' is not enabled")

        # Special deterministic stubs for tests (no real calls).
        if prov.name == "openai":
            # Override generate() with a deterministic stub.
            class _OpenAIStub(Provider):
                def generate(self, *, prompt: str, context: Dict[str, Any]):
                    return f"[openai stub] {prompt}"

            return cls(_default=_OpenAIStub(name="openai", enabled=True))

        # Other providers: enabled but still silent by default in 10E.
        return cls(_default=prov)

    def get_default(self) -> Provider:
        return self._default
