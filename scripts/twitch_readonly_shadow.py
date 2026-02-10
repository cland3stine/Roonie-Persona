from __future__ import annotations

import sys

# Ensure Windows console doesn't crash on Unicode output (emoji, etc.)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # py3.7+
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import time
from datetime import datetime
from typing import Any, Dict

from src.twitch.read_path import iter_twitch_messages
from src.roonie.network.transports_urllib import UrllibJsonTransport
from src.providers.openai_real import OpenAIProvider
from src.providers.anthropic_real import AnthropicProvider
from src.providers.grok_real import GrokProvider
from src.providers.shadow_log import ShadowLogConfig, log_shadow
from src.roonie.prompting import build_roonie_prompt

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

    # Provider keys
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    grok_key = (os.getenv("GROK_API_KEY") or "").strip()

    # Twitch creds
    oauth = (os.getenv("TWITCH_OAUTH_TOKEN") or "").strip()
    nick = (os.getenv("TWITCH_NICK") or "").strip()
    chan = (os.getenv("TWITCH_CHANNEL") or "").strip()

    missing = []
    for k, v in [
        ("OPENAI_API_KEY", openai_key),
        ("ANTHROPIC_API_KEY", anthropic_key),
        ("GROK_API_KEY", grok_key),
        ("TWITCH_OAUTH_TOKEN", oauth),
        ("TWITCH_NICK", nick),
        ("TWITCH_CHANNEL", chan),
    ]:
        if not v:
            missing.append(k)
    if missing:
        print("ERROR: missing:", ", ".join(missing))
        return 2

    user_agent = os.getenv("ROONIE_USER_AGENT") or "roonie-ai/twitch-readonly"
    timeout_s = int(os.getenv("ROONIE_HTTP_TIMEOUT_SECONDS") or "15")
    transport = UrllibJsonTransport(user_agent=user_agent, timeout_seconds=timeout_s)

    primary = OpenAIProvider(name="openai", enabled=True, transport=transport, api_key=openai_key)
    claude = AnthropicProvider(name="anthropic", enabled=True, transport=transport, api_key=anthropic_key)
    grok = GrokProvider(name="grok", enabled=True, transport=transport, api_key=grok_key)

    max_msgs = int(os.getenv("ROONIE_TWITCH_MAX_MSGS") or "30")
    max_seconds = int(os.getenv("ROONIE_TWITCH_MAX_SECONDS") or "900")  # 15 min default

    log_path = repo_root / "logs" / "shadow_runs" / f"{datetime.now().strftime('%Y-%m-%d')}_twitch_readonly_{_now_tag()}.jsonl"
    log_cfg = ShadowLogConfig(path=log_path, log_full_text_filtered=True, odd_latency_ms=2500)

    print("Twitch readonly shadow run:")
    print(" - NO POSTING (read-only client)")
    print(" - Primary: openai (prints candidate)")
    print(" - Shadows: anthropic + grok (JSONL shadow log, filtered full text)")
    print("Log:", log_path)
    print("Limits:", {"max_msgs": max_msgs, "max_seconds": max_seconds})
    print("Idle timeout seconds:", int(os.getenv("ROONIE_TWITCH_IDLE_SECONDS") or "15"))
    print("Channel:", chan)

    start = time.time()
    idle_deadline = start + int(os.getenv("ROONIE_TWITCH_IDLE_SECONDS") or "15")

    count = 0

    for msg in iter_twitch_messages(oauth_token=oauth, nick=nick, channel=chan, debug=True):
        # stop conditions
        if count >= max_msgs:
            print("STOP: max_msgs reached")
            break
        if (time.time() - start) >= max_seconds:
            print("STOP: max_seconds reached")
            break

        count += 1
        text = msg.message

        flags: Dict[str, Any] = {
            "test_marker": text.startswith("TEST:"),
            "mention": "@roonie" in text.lower(),
            "sarcasm": ("" in text) or ("sure buddy" in text.lower()),
        }

        # Primary candidate (printed only)
        t0 = time.perf_counter()
        prompt = build_roonie_prompt(message=text, metadata={"viewer": msg.nick, "channel": chan})
        out_primary = primary.generate(prompt=prompt, context={"model": os.getenv("OPENAI_MODEL") or "gpt-5.2"})
        lat_primary = int((time.perf_counter() - t0) * 1000)
        print(f"\n#{count} {msg.nick}: {text}")
        print(f"PRIMARY(openai) latency_ms={lat_primary}")
        print(out_primary or "(None)")

        # Shadows (logged)
        for prov, obj, model_env in [
            ("anthropic", claude, "CLAUDE_MODEL"),
            ("grok", grok, "GROK_MODEL"),
        ]:
            t0 = time.perf_counter()
            err = None
            out = None
            try:
                out = obj.generate(
                    prompt=prompt,
                    context={"model": os.getenv(model_env) or "", "max_tokens": int(os.getenv("ROONIE_MAX_OUTPUT_TOKENS") or "140")},
                )
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            lat = int((time.perf_counter() - t0) * 1000)
            log_shadow(
                cfg=log_cfg,
                provider=prov,
                event="twitch_readonly",
                prompt=text,
                context_flags=flags,
                latency_ms=lat,
                error=err,
                output_text=out,
            )
            print(f"SHADOW({prov}) latency_ms={lat} err={err!r} logged=yes")

    print("\nDONE.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
