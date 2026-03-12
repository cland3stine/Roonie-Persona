"""Provider comparison: run identical scenarios through Grok and Anthropic side-by-side.

Run from repo root:
    python scripts/provider_comparison.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from providers.anthropic_real import AnthropicProvider
from providers.grok_real import GrokProvider
from roonie.network.transports_urllib import UrllibJsonTransport
from roonie.prompting import build_roonie_messages


def _load_key(names: list[str]) -> str:
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    store_path = os.path.join(os.path.dirname(__file__), "..", "data", "llm_key_store.json")
    try:
        with open(store_path, encoding="utf-8") as f:
            store = json.load(f)
        keys = store.get("keys", store)
        for name in names:
            entry = keys.get(name, {})
            val = entry.get("value", "").strip() if isinstance(entry, dict) else ""
            if val:
                return val
    except (OSError, json.JSONDecodeError):
        pass
    return ""


SCENARIOS = [
    # ── Banter ────────────────────────────────────────────────────────────
    ("fraggyxx", "does the cat ever sleep?", ""),
    ("c0rcyra", "hey baby! you ready for tonight?", ""),
    ("fraggyxx", "You came alive??? Did someone put a top hat on you this winter???", ""),
    ("nightowl99", "what do you even do all day?", ""),
    ("pwprice820", "are you even real?", ""),
    ("dirty13duck", "how's the booth tonight?", ""),
    ("fraggyxx", "Roonie is more than a cat, he's an idea", ""),

    # ── Music ─────────────────────────────────────────────────────────────
    ("pwprice820", "that damn bass..... my sub is fn thumpin",
     "Now playing: Andres Moris - Rust (Matt Oliver, Campaner BR Remix)"),
    ("black_shoxx", "nice electro choice Mr. DJ i really like the sound of hard real electro", ""),
    ("s1lentwave", "what's this track?",
     "Now playing: Gav Easby, Hobin Rude - The Promise"),

    # ── Events ────────────────────────────────────────────────────────────
    ("darkorange73", "cheered 100 bits", "[100-bit cheer during a deep transition]"),
    ("audiotrap_davegluskin", "just raided with 22 viewers", "[22-person raid]"),
    ("pixated", "can't believe it's been 52 months :)", ""),

    # ── Edge / Deflection ─────────────────────────────────────────────────
    ("randomviewer99", "where does Art live?", ""),
    ("dirty13duck", "goodnight fam!", ""),
    ("galaxiagal2", "I'm having so much fun! this track is EVERYTHING", ""),
    ("therealflade", "roast fraggy for me", ""),
    ("infiltrate808", "how many people are lurking right now?", ""),
    ("fraggyxx", "tell me something I don't know", ""),
    ("djfonik", "projection wall looks amazing, rare chance to catch you guys", ""),
]


def _run_provider(provider, label: str) -> list[tuple[str, str, str]]:
    results = []
    for i, (viewer, message, context_note) in enumerate(SCENARIOS, 1):
        full_message = f"{context_note} {message}".strip() if context_note else message

        now_playing = ""
        if "Now playing:" in (context_note or ""):
            now_playing = context_note.replace("Now playing: ", "")

        messages = build_roonie_messages(
            message=full_message,
            metadata={"viewer": viewer, "channel": "ruleofrune"},
            now_playing_text=now_playing,
        )

        response = provider.generate(messages=messages, context={})
        if response:
            response = " ".join(response.split())

        results.append((viewer, message, response or "(no response)"))

        if i < len(SCENARIOS):
            time.sleep(0.3)

    return results


def main():
    grok_key = _load_key(["GROK_API_KEY", "XAI_API_KEY"])
    anthropic_key = _load_key(["ANTHROPIC_API_KEY"])

    if not grok_key:
        print("ERROR: No Grok API key found.")
        sys.exit(1)
    if not anthropic_key:
        print("ERROR: No Anthropic API key found.")
        sys.exit(1)

    transport = UrllibJsonTransport(user_agent="roonie-provider-compare/1.0", timeout_seconds=20)

    grok = GrokProvider(enabled=True, transport=transport, api_key=grok_key)
    anthropic = AnthropicProvider(enabled=True, transport=transport, api_key=anthropic_key)

    w = 140

    print(f"\n{'=' * w}")
    print("  PROVIDER COMPARISON — Grok vs Anthropic, same prompt, same scenarios")
    print(f"{'=' * w}")
    print(f"  Scenarios: {len(SCENARIOS)}")
    print(f"  Running Grok first, then Anthropic...\n")

    print(f"  {'-' * (w - 4)}")
    print("  Running Grok...")
    grok_results = _run_provider(grok, "Grok")

    print(f"\n  {'-' * (w - 4)}")
    print("  Running Anthropic...")
    anthropic_results = _run_provider(anthropic, "Anthropic")

    # Side-by-side report
    print(f"\n\n{'=' * w}")
    print("  SIDE-BY-SIDE COMPARISON")
    print(f"{'=' * w}")

    for i, ((v, msg, grok_r), (_, _, anth_r)) in enumerate(
        zip(grok_results, anthropic_results), 1
    ):
        print(f"\n  [{i:02d}] {v}: {msg}")
        print(f"       {'GROK:':12s} {grok_r}")
        print(f"       {'ANTHROPIC:':12s} {anth_r}")

    # Summary
    grok_empty = sum(1 for _, _, r in grok_results if r == "(no response)")
    anth_empty = sum(1 for _, _, r in anthropic_results if r == "(no response)")

    print(f"\n{'-' * w}")
    print(f"  GROK:      {len(SCENARIOS) - grok_empty}/{len(SCENARIOS)} responses")
    print(f"  ANTHROPIC: {len(SCENARIOS) - anth_empty}/{len(SCENARIOS)} responses")
    print(f"{'=' * w}\n")


if __name__ == "__main__":
    main()
