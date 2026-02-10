from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers.registry import ProviderRegistry
from src.roonie.live_director import LiveDirector
from src.roonie.types import Env, Event


def main() -> int:
    cfg = {
        "default_provider": "openai",
        "providers": {
            "openai": {"enabled": True},
            "anthropic": {"enabled": True},
            "grok": {"enabled": True},
        },
    }
    registry = ProviderRegistry.from_dict(cfg)
    routing_cfg = {"shadow_enabled": True, "shadow_provider": "grok"}

    director = LiveDirector(registry=registry, routing_cfg=routing_cfg)
    env = Env(offline=False)

    events = [
        Event(event_id="L1", message="TEST: hello roonie", actor="viewer", metadata={"case_id": "live"}),
        Event(
            event_id="L2",
            message="@roonie sure buddy ",
            actor="viewer",
            metadata={"case_id": "live", "sarcasm": True},
        ),
    ]
    for event in events:
        decision = director.evaluate(event, env)
        print(decision.action, decision.route, decision.response_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
