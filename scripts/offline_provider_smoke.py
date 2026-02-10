from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers.anthropic_real import AnthropicProvider
from src.providers.grok_real import GrokProvider
from src.providers.openai_real import OpenAIProvider
from src.providers.shadow_log import ShadowLogConfig, log_shadow
from src.roonie.network.transports_urllib import UrllibJsonTransport


def _load_secrets_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Do not overwrite already-exported env values.
        os.environ.setdefault(key, value)


def _now_tag() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    secrets_path = repo_root / "config" / "secrets.env"
    _load_secrets_env(secrets_path)

    # Keys are loaded locally only and never printed.
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    grok_key = (os.getenv("GROK_API_KEY") or "").strip()

    missing = []
    if not openai_key:
        missing.append("OPENAI_API_KEY")
    if not anthropic_key:
        missing.append("ANTHROPIC_API_KEY")
    if not grok_key:
        missing.append("GROK_API_KEY")
    if missing:
        print("ERROR: missing keys:", ", ".join(missing))
        print("Fill them in config/secrets.env (local only) or export env vars.")
        return 2

    user_agent = os.getenv("ROONIE_USER_AGENT") or "roonie-ai/phase11a"
    timeout_s = int(os.getenv("ROONIE_HTTP_TIMEOUT_SECONDS") or "15")

    transport = UrllibJsonTransport(user_agent=user_agent, timeout_seconds=timeout_s)

    primary = OpenAIProvider(name="openai", enabled=True, transport=transport, api_key=openai_key)
    claude = AnthropicProvider(name="anthropic", enabled=True, transport=transport, api_key=anthropic_key)
    grok = GrokProvider(name="grok", enabled=True, transport=transport, api_key=grok_key)

    prompt = "TEST: offline smoke (primary=openai, shadows=anthropic+grok). Reply with ONE short sentence."
    ctx: Dict[str, Any] = {"test_marker": True, "mention": False, "sarcasm": False}

    log_path = repo_root / "logs" / "shadow_runs" / f"{datetime.now().strftime('%Y-%m-%d')}_offline_smoke_{_now_tag()}.jsonl"
    log_cfg = ShadowLogConfig(path=log_path, log_full_text_filtered=True, odd_latency_ms=2500)

    print("Running offline smoke:")
    print("- Primary: openai (will print output)")
    print("- Shadow: anthropic + grok (logged to JSONL, full text filtered)")
    print("Log:", log_path)

    t0 = time.perf_counter()
    out_primary = primary.generate(prompt=prompt, context={"model": os.getenv("OPENAI_MODEL") or "gpt-5.2"})
    lat_primary = int((time.perf_counter() - t0) * 1000)
    print(f"\nPRIMARY (openai) latency_ms={lat_primary}")
    print(out_primary or "(None)")

    for provider_name, provider_obj, model_env in [
        ("anthropic", claude, "CLAUDE_MODEL"),
        ("grok", grok, "GROK_MODEL"),
    ]:
        t0 = time.perf_counter()
        err = None
        text = None
        try:
            text = provider_obj.generate(
                prompt=prompt,
                context={
                    "model": os.getenv(model_env) or "",
                    "max_tokens": int(os.getenv("ROONIE_MAX_OUTPUT_TOKENS") or "140"),
                },
            )
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        lat = int((time.perf_counter() - t0) * 1000)
        log_shadow(
            cfg=log_cfg,
            provider=provider_name,
            event="offline_smoke",
            prompt=prompt,
            context_flags=ctx,
            latency_ms=lat,
            error=err,
            output_text=text,
        )
        print(f"SHADOW ({provider_name}) latency_ms={lat} err={err!r} logged=yes")

    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
