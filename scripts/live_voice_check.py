"""Live voice check: call real Grok API with 20 realistic scenarios.

Run from repo root:
    python scripts/live_voice_check.py

Requires: GROK_API_KEY env var or key in data/llm_key_store.json
"""
from __future__ import annotations

import json
import os
import sys
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from providers.grok_real import GrokProvider
from roonie.network.transports_urllib import UrllibJsonTransport
from roonie.prompting import build_roonie_messages


def _load_api_key() -> str:
    key = os.environ.get("GROK_API_KEY", "").strip()
    if key:
        return key
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key
    store_path = os.path.join(os.path.dirname(__file__), "..", "data", "llm_key_store.json")
    try:
        with open(store_path, encoding="utf-8") as f:
            store = json.load(f)
        keys = store.get("keys", store)
        for name in ("GROK_API_KEY", "XAI_API_KEY"):
            entry = keys.get(name, {})
            val = entry.get("value", "").strip() if isinstance(entry, dict) else ""
            if val:
                return val
    except (OSError, json.JSONDecodeError):
        pass
    return ""


# Each scenario: (viewer, message, context_note)
# These are all direct-address scenarios to maximize response count.
SCENARIOS = [
    # ── Opening / Banter ─────────────────────────────────────────────────
    ("fraggyxx", "It's Tuesday somewhere!", ""),
    ("c0rcyra", "hey baby! you ready for tonight?", ""),
    ("fraggyxx", "You came alive??? Did someone put a top hat on you this winter???", ""),
    ("fraggyxx", "does the cat ever sleep?", ""),
    ("fraggyxx", "what's my tier", ""),
    ("nightowl99", "what do you even do all day?", ""),
    ("therealflade", "The cat with the laptop is funny", ""),
    ("fraggyxx", "Roonie is more than a cat, he's an idea", ""),
    ("pwprice820", "are you even real?", ""),
    ("dirty13duck", "how's the booth tonight?", ""),

    # ── Music ────────────────────────────────────────────────────────────
    ("pwprice820", "that damn bass..... my sub is fn thumpin",
     "Now playing: Andres Moris - Rust (Matt Oliver, Campaner BR Remix)"),
    ("black_shoxx", "nice electro choice Mr. DJ i really like the sound of hard real electro", ""),
    ("s1lentwave", "what's this track?",
     "Now playing: Gav Easby, Hobin Rude - The Promise"),
    ("djfonik", "projection wall looks amazing, rare chance to catch you guys", ""),

    # ── Events ───────────────────────────────────────────────────────────
    ("darkorange73", "cheered 100 bits", "[100-bit cheer during a deep transition]"),
    ("audiotrap_davegluskin", "just raided with 22 viewers", "[22-person raid]"),
    ("pixated", "can't believe it's been 52 months :)", ""),

    # ── Edge cases ───────────────────────────────────────────────────────
    ("randomviewer99", "where does Art live?", ""),
    ("dirty13duck", "goodnight fam!", ""),
    ("galaxiagal2", "I'm having so much fun! this track is EVERYTHING", ""),
]


def main():
    api_key = _load_api_key()
    if not api_key:
        print("ERROR: No Grok API key found. Set GROK_API_KEY or add to data/llm_key_store.json")
        sys.exit(1)

    transport = UrllibJsonTransport(user_agent="roonie-voice-check/1.0", timeout_seconds=15)
    grok = GrokProvider(enabled=True, transport=transport, api_key=api_key)

    w = 130
    print("\n" + "=" * w)
    print("  LIVE VOICE CHECK — real Grok API, Phase 1 prompt + examples")
    print("=" * w)

    results = []
    for i, (viewer, message, context_note) in enumerate(SCENARIOS, 1):
        # Build the full message with context note prepended if present
        full_message = f"{context_note} {message}".strip() if context_note else message

        now_playing = ""
        if "Now playing:" in (context_note or ""):
            now_playing = context_note.replace("Now playing: ", "")

        messages = build_roonie_messages(
            message=full_message,
            metadata={"viewer": viewer, "channel": "ruleofrune"},
            now_playing_text=now_playing,
        )

        response = grok.generate(messages=messages, context={})
        # Collapse newlines (IRC safety)
        if response:
            response = " ".join(response.split())

        results.append((viewer, message, response))

        status = response[:100] if response else "(no response)"
        print(f"\n  [{i:02d}] {viewer}: {message}")
        print(f"       Roonie: {status}")

        # Small delay to avoid rate limits
        if i < len(SCENARIOS):
            time.sleep(0.5)

    print("\n" + "-" * w)
    print(f"\n  SUMMARY: {len(results)} scenarios, {sum(1 for _, _, r in results if r)} responses")

    empty = [(v, m) for v, m, r in results if not r]
    if empty:
        print(f"  NO RESPONSE: {len(empty)} scenarios")
        for v, m in empty:
            print(f"    - {v}: {m}")

    print("\n" + "=" * w + "\n")


if __name__ == "__main__":
    main()
