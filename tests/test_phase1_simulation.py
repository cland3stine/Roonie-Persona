"""Phase 1 simulation: 40 real-stream scenarios exercising the full pipeline.

Run: pytest tests/test_phase1_simulation.py -vs

Exercises: COMPRESSED_STYLE, EXAMPLE_BANK, native messages, specificity gate
(active mode), event handling, continuation, [SKIP], LOW_CONTENT, mid-sentence
name detection. Uses real viewer names and messages from the 2026-03-05 stream.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event

SESSION = "sim-phase1"

# ---------------------------------------------------------------------------
# Ordered fake LLM responses — consumed sequentially by route_generate stub.
# Only scenarios that reach the LLM consume an entry.
# ---------------------------------------------------------------------------
FAKE_LLM_RESPONSES = [
    # 0: s03 fraggyxx banter
    "@fraggyxx it is Tuesday somewhere and that somewhere is right here on the booth.",
    # 1: s04 c0rcyra greeting
    "@c0rcyra hey Jen. paws are warmed up, let's go.",
    # 2: s07 RAID 22 viewers
    "@audiotrap_davegluskin 22 deep? solid entrance.",
    # 3: s08 audiotrap_davegluskin continuation after RAID
    "@audiotrap_davegluskin doing great! you landed right in the middle of a solid set.",
    # 4: s09 CHEER 100 bits
    "@darkorange73 100 bits right as that bassline dropped in? timing.",
    # 4: s11 fraggyxx banter
    "@fraggyxx no top hat, just the booth running hot tonight. paws are warmed up.",
    # 5: s12 pwprice820 music
    "@pwprice820 yeah the sub's earning its keep on this one.",
    # 6: s14 fraggyxx tier
    "@fraggyxx you're tier infinity. it's a custom thing.",
    # 7: s15 pixated months
    "@pixated 52 months. you're basically wired into the booth at this point.",
    # 8: s16 djfonik visit
    "@djfonik rare Thursday set. glad the timing worked out.",
    # 9: s17 therealflade laptop
    "@therealflade Jen's laptop. tiny keys, big paws. it's a whole thing.",
    # 10: s18 fraggyxx idea
    "@fraggyxx more of an idea than a cat? I'll take it.",
    # 11: s19 !id TRACK_ID exempt
    "@pwprice820 that's Gav Easby, Hobin Rude - The Promise.",
    # 12: s20 pwprice820 bass — specific music anchor
    "@pwprice820 Campaner does that low-end thing where it sneaks up on you.",
    # 13: s21 black_shoxx electro
    "@black_shoxx yeah this one's got that sharp little edge without losing the swing.",
    # 14: s22 s1lentwave TRACK_ID exempt
    "@s1lentwave it's on Sudbeat, solid progressive vibes.",
    # 15: s24 infiltrate808 lenses
    "@infiltrate808 samyang glass on the a7? that's a solid combo.",
    # 16: s25 infiltrate808 continuation exempt
    "@infiltrate808 yeah the 135 is wild for portraits at that aperture.",
    # 17: s26 infiltrate808 [SKIP]
    "[SKIP]",
    # 18: s29 darkorange73 → GENERIC (gate catches)
    "@darkorange73 Welcome in! Good to see you tonight!",
    # 19: s30 sunnyfox99 → GENERIC (gate catches)
    "@sunnyfox99 Thank you, glad you're here! Settle in!",
    # 20: s31 gooiefrenchtoast → GENERIC (gate catches)
    "@gooiefrenchtoast Welcome! Good to see you!",
    # 21: s32 ajuna2 → specific with music anchor (passes)
    "@ajuna2 hey. you just missed a real smooth transition.",
    # 22: s33 queenbananabean → GENERIC (gate catches)
    "@queenbananabean means a lot that you're here!",
    # 23: s34 mnascimento1979 → GENERIC (gate catches)
    "@mnascimento1979 Welcome in, glad you found us!",
    # 24: s35 dirty13duck → specific (passes)
    "@dirty13duck 5 gifts? the duck doesn't play around.",
    # 25: s37 pwprice820 sleep
    "@pwprice820 never.",
    # 26: s38 therealflade → GENERIC (gate catches)
    "@therealflade Thank you for the support! Good to see you!",
    # 27: s39 dirty13duck goodnight
    "@dirty13duck night. this was a good set.",
    # 28: s40 darkorange73 night
    "@darkorange73 night. solid set from the first track.",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(
    event_id: str,
    message: str,
    *,
    user: str,
    is_direct_mention: bool = False,
    metadata_extra: Dict[str, Any] | None = None,
) -> Event:
    metadata: Dict[str, Any] = {
        "user": user,
        "is_direct_mention": is_direct_mention,
        "mode": "live",
        "platform": "twitch",
        "session_id": SESSION,
    }
    if isinstance(metadata_extra, dict):
        metadata.update(metadata_extra)
    return Event(event_id=event_id, message=message, metadata=metadata)


def _make_stub(monkeypatch, responses):
    state = {"idx": 0, "calls": []}

    def _stub(**kwargs):
        ctx = kwargs["context"]
        ctx["provider_selected"] = "grok"
        ctx["moderation_result"] = "allow"
        idx = state["idx"]
        resp = responses[idx] if idx < len(responses) else "[EXHAUSTED]"
        state["idx"] += 1
        state["calls"].append({"idx": idx, "response": resp})
        return resp

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub)
    return state


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------

class S:
    __slots__ = (
        "sid", "user", "message", "mention", "metadata_extra",
        "expected_action", "note", "result",
    )

    def __init__(
        self, sid, user, message, *,
        mention=False, metadata_extra=None,
        expected_action="NOOP", note="",
    ):
        self.sid = sid
        self.user = user
        self.message = message
        self.mention = mention
        self.metadata_extra = metadata_extra
        self.expected_action = expected_action
        self.note = note
        self.result = None


SCENARIOS: List[S] = [
    # ── Opening ──────────────────────────────────────────────────────────
    S("s01", "dirty13duck", "You fam!!!! So excited. It has been a minute for me.",
      note="Greeting to room"),
    S("s02", "djleonelgodoy", "Saludo de argentina",
      note="Non-English greeting to room"),
    S("s03", "fraggyxx", "@RoonieTheCat It's Tuesday somewhere!",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, playful banter"),
    S("s04", "c0rcyra", "@RoonieTheCat hey baby! you ready for tonight?",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Jen greets Roonie directly"),
    S("s05", "galaxiagal2", "Hi fam!",
      note="Greeting to room"),
    S("s06", "pwprice820", "RoR fam",
      note="Short greeting to room"),

    # ── Events ───────────────────────────────────────────────────────────
    S("s07", "audiotrap_davegluskin",
      "@RoonieTheCat heads up: audiotrap_davegluskin just raided with 22 viewers.",
      mention=True, expected_action="RESPOND_PUBLIC",
      metadata_extra={"event_type": "RAID"},
      note="22-viewer RAID event"),
    S("s08", "audiotrap_davegluskin", "always!! hope you guys are doing well!!",
      expected_action="RESPOND_PUBLIC",
      note="Continuation: same viewer follow-up after RAID response"),
    S("s09", "darkorange73",
      "@RoonieTheCat heads up: darkorange73 cheered 100 bits.",
      mention=True, expected_action="RESPOND_PUBLIC",
      metadata_extra={"event_type": "CHEER"},
      note="100-bit CHEER event"),

    # ── Banter ───────────────────────────────────────────────────────────
    S("s10", "therealflade", "Hehehe. The cat with the laptop is funny. LUL",
      note="Third-person mention, not addressed"),
    S("s11", "fraggyxx",
      "@RoonieTheCat You came alive??? Did someone put a top hat on you this winter???",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, banter"),
    S("s12", "pwprice820", "@RoonieTheCat that damn bass..... my sub is fn thumpin",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, music reaction"),
    S("s13", "die__eine__", "therea4493Beat therea4493Tekki therea4493Beat",
      note="Emote-only, no text"),
    S("s14", "fraggyxx", "@RoonieTheCat what's my tier",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, playful question"),
    S("s15", "pixated", "@RoonieTheCat can't believe it's been 52 months :)",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, sub milestone"),
    S("s16", "djfonik", "@RoonieTheCat rare chance to catch you guys, normally yall play when im working",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, schedule comment"),
    S("s17", "therealflade", "@RoonieTheCat The cat with the laptop is funny",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention about laptop joke"),
    S("s18", "fraggyxx", "Roonie is more than a cat, he's an idea",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Mid-sentence name detection"),

    # ── Music ────────────────────────────────────────────────────────────
    S("s19", "pwprice820", "@RoonieTheCat what track is this?",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, track ID question"),
    S("s20", "pwprice820", "@RoonieTheCat Campaner.... that damn bass",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, music comment"),
    S("s21", "black_shoxx",
      "@RoonieTheCat nice electro choice Mr. DJ i really like the sound of hard real electro",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, music observation"),
    S("s22", "s1lentwave", "@RoonieTheCat what's this track?",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, track ID question"),
    S("s23", "mixmaster_k", "this transition is insane",
      note="Music comment to room, no mention"),

    # ── Continuation ─────────────────────────────────────────────────────
    S("s24", "infiltrate808",
      "@RoonieTheCat I am shopping for lenses for the a7 v as i listen",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, camera gear banter"),
    S("s25", "infiltrate808", "Samyang 135mm F1.8",
      expected_action="RESPOND_PUBLIC",
      note="Continuation: same viewer follow-up, no intervening messages"),
    S("s26", "infiltrate808", "this is better than 85mm",
      expected_action="NOOP",
      note="Continuation: LLM returns [SKIP], conversation ends naturally"),
    S("s27", "purrzia", "graemerzHeart graemerzHeart graemerzHeart",
      note="Emote-only bystander"),
    S("s28", "infiltrate808", ":)",
      note="LOW_CONTENT blocks continuation"),

    # ── Specificity Gate Stress ──────────────────────────────────────────
    S("s29", "darkorange73", "@RoonieTheCat good evening",
      mention=True, expected_action="NOOP",
      note="GATE: 'Welcome in! Good to see you tonight!'"),
    S("s30", "sunnyfox99", "@RoonieTheCat Happy Thursday!",
      mention=True, expected_action="NOOP",
      note="GATE: 'Thank you, glad you're here! Settle in!'"),
    S("s31", "gooiefrenchtoast", "@RoonieTheCat Haiiiii",
      mention=True, expected_action="NOOP",
      note="GATE: 'Welcome! Good to see you!'"),
    S("s32", "ajuna2", "@RoonieTheCat hey hey",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Music anchor 'transition' saves from gate"),
    S("s33", "queenbananabean", "@RoonieTheCat I should bake",
      mention=True, expected_action="NOOP",
      note="GATE: 'means a lot that you're here!'"),
    S("s34", "mnascimento1979", "@RoonieTheCat good evening",
      mention=True, expected_action="NOOP",
      note="GATE: 'Welcome in, glad you found us!'"),
    S("s35", "dirty13duck", "@RoonieTheCat 5 gifted subs earlier, not bad right?",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Specific response, no generic patterns"),

    # ── Late Stream ──────────────────────────────────────────────────────
    S("s36", "galaxiagal2", "I'm having so much fun!",
      note="Excited comment to room"),
    S("s37", "pwprice820", "@RoonieTheCat does the cat ever sleep?",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Direct mention, playful question"),
    S("s38", "therealflade", "@RoonieTheCat 7 months and counting",
      mention=True, expected_action="NOOP",
      note="GATE: 'Thank you for the support! Good to see you!'"),
    S("s39", "dirty13duck", "@RoonieTheCat goodnight fam!",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Farewell, specific response"),
    S("s40", "darkorange73", "@RoonieTheCat night! great set",
      mention=True, expected_action="RESPOND_PUBLIC",
      note="Farewell, specific response with music anchor"),
]


# ---------------------------------------------------------------------------
# Simulation test
# ---------------------------------------------------------------------------

def test_phase1_simulation(monkeypatch):
    """Full Phase 1 pipeline simulation with 40 real-stream scenarios."""
    monkeypatch.setenv("ROONIE_SPECIFICITY_GATE_MODE", "active")
    stub = _make_stub(monkeypatch, FAKE_LLM_RESPONSES)
    d = ProviderDirector()
    env = Env(offline=False)

    report: List[str] = []
    mismatches: List[Tuple[str, str, str, str]] = []

    for sc in SCENARIOS:
        e = _event(
            sc.sid, sc.message, user=sc.user,
            is_direct_mention=sc.mention, metadata_extra=sc.metadata_extra,
        )
        result = d.evaluate(e, env)
        sc.result = result

        if result.action == "RESPOND_PUBLIC":
            d.apply_output_feedback(
                event_id=sc.sid, emitted=True, send_result={"sent": True},
            )

        # Extract trace info
        trace = result.trace or {}
        dt = trace.get("director", {})
        spec = trace.get("specificity", {})
        cont = dt.get("conversation_continuation", False)
        cont_skip = dt.get("continuation_skipped", False)

        # Build display
        action_col = result.action
        response_col = ""

        if result.action == "RESPOND_PUBLIC" and result.response_text:
            response_col = result.response_text[:90]
        elif spec.get("suppressed"):
            action_col = "GATED"
            hits = spec.get("generic_hits", [])
            response_col = f"BLOCKED {hits}"
        elif cont_skip:
            response_col = "[SKIP] conversation ending"
        elif cont and result.action == "NOOP":
            reason = dt.get("continuation_reason", "")
            response_col = f"(continuation blocked: {reason})"
        else:
            response_col = f"({sc.note})" if sc.note else ""

        tag = ""
        if cont and result.action == "RESPOND_PUBLIC":
            tag = " [cont]"
        elif spec.get("exempt_reason"):
            tag = f" [exempt:{spec['exempt_reason']}]"

        report.append(
            f"  {sc.sid}  {sc.user:<22s}  {sc.message[:52]:<52s}  {action_col:8s}  {response_col}{tag}"
        )

        if result.action != sc.expected_action:
            mismatches.append((
                sc.sid, sc.expected_action, result.action, sc.message[:50],
            ))

    # Print report
    w = 160
    print("\n" + "=" * w)
    print("  PHASE 1 SIMULATION REPORT  |  specificity gate: ACTIVE  |  provider: grok (fake)")
    print("=" * w)
    print(f"  {'ID':<4s}  {'USER':<22s}  {'MESSAGE':<52s}  {'ACTION':8s}  ROONIE OUTPUT")
    print("-" * w)
    for line in report:
        print(line)
    print("-" * w)

    total = len(SCENARIOS)
    responded = sum(1 for s in SCENARIOS if s.result and s.result.action == "RESPOND_PUBLIC")
    gated = sum(1 for s in SCENARIOS if s.result and (s.result.trace or {}).get("specificity", {}).get("suppressed"))
    skipped = sum(1 for s in SCENARIOS if s.result and (s.result.trace or {}).get("director", {}).get("continuation_skipped"))
    noop_no_llm = total - stub["idx"] - (total - sum(1 for s in SCENARIOS if s.result))

    print(f"\n  TOTALS: {total} scenarios | {responded} responded | {gated} gate-blocked | {skipped} [SKIP] | LLM calls: {stub['idx']}")
    print("=" * w + "\n")

    if mismatches:
        msg = "\n".join(f"  [{sid}] expected={exp} actual={act}: {m}" for sid, exp, act, m in mismatches)
        pytest.fail(f"{len(mismatches)} mismatches:\n{msg}")
