from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import time
from datetime import datetime
from typing import Any, Dict

from roonie.network.transports_urllib import UrllibJsonTransport
from providers.openai_real import OpenAIProvider
from providers.anthropic_real import AnthropicProvider
from providers.grok_real import GrokProvider
from providers.shadow_log import ShadowLogConfig, log_shadow

def _load_secrets_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

def _now_tag() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")

def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    _load_secrets_env(repo_root / "config" / "secrets.env")

    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    grok_key = (os.getenv("GROK_API_KEY") or "").strip()

    missing = []
    if not openai_key: missing.append("OPENAI_API_KEY")
    if not anthropic_key: missing.append("ANTHROPIC_API_KEY")
    if not grok_key: missing.append("GROK_API_KEY")
    if missing:
        print("ERROR: missing keys:", ", ".join(missing))
        return 2

    user_agent = os.getenv("ROONIE_USER_AGENT") or "roonie-ai/phase11b"
    timeout_s = int(os.getenv("ROONIE_HTTP_TIMEOUT_SECONDS") or "15")
    transport = UrllibJsonTransport(user_agent=user_agent, timeout_seconds=timeout_s)

    primary = OpenAIProvider(name="openai", enabled=True, transport=transport, api_key=openai_key)
    claude = AnthropicProvider(name="anthropic", enabled=True, transport=transport, api_key=anthropic_key)
    grok = GrokProvider(name="grok", enabled=True, transport=transport, api_key=grok_key)

    log_path = repo_root / "logs" / "shadow_runs" / f"{datetime.now().strftime('%Y-%m-%d')}_live_readonly_{_now_tag()}.jsonl"
    log_cfg = ShadowLogConfig(path=log_path, log_full_text_filtered=True, odd_latency_ms=2500)

    # Simulated "closed chat" events (we'll replace with real Twitch reader in 11B.2)
    events = [
        ("L1", "TEST: hello roonie (live readonly)"),
        ("L2", "@roonie sure buddy "),
        ("L3", "TEST: what camera are you using?"),
    ]

    print("Live readonly (real providers):")
    print(" - Primary: openai (prints)")
    print(" - Shadows: anthropic + grok (JSONL shadow log, filtered full text)")
    print("Log:", log_path)

    for event_id, msg in events:
        flags: Dict[str, Any] = {
            "test_marker": msg.startswith("TEST:"),
            "mention": "@roonie" in msg.lower(),
            "sarcasm": "" in msg,
        }
        prompt = msg

        # Primary
        t0 = time.perf_counter()
        out_primary = primary.generate(prompt=prompt, context={"model": os.getenv("OPENAI_MODEL") or "gpt-5.2"})
        lat_primary = int((time.perf_counter() - t0) * 1000)
        print(f"\nEVENT {event_id} PRIMARY(openai) latency_ms={lat_primary}")
        print(out_primary or "(None)")

        # Shadows
        for prov, obj, model_env in [
            ("anthropic", claude, "CLAUDE_MODEL"),
            ("grok", grok, "GROK_MODEL"),
        ]:
            t0 = time.perf_counter()
            err = None
            text = None
            try:
                text = obj.generate(
                    prompt=prompt,
                    context={"model": os.getenv(model_env) or "", "max_tokens": int(os.getenv("ROONIE_MAX_OUTPUT_TOKENS") or "140")},
                )
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            lat = int((time.perf_counter() - t0) * 1000)
            log_shadow(
                cfg=log_cfg,
                provider=prov,
                event="live_readonly",
                prompt=prompt,
                context_flags=flags,
                latency_ms=lat,
                error=err,
                output_text=text,
            )
            print(f"EVENT {event_id} SHADOW({prov}) latency_ms={lat} err={err!r} logged=yes")

    print("\nDONE.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
