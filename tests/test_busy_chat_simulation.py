"""Comprehensive busy-chat simulation: 12 viewers, 65+ messages, realistic Twitch patterns.

Simulates a full Saturday stream session to stress-test conversation continuation,
topic latching, thread handoffs, greeting guards, natural expiry, safety cap,
and cross-talk filtering. Each message is annotated with expected behavior and
the reasoning behind it.

Cast:
    c0rcyra       — Jen (Art's partner), regular, talks to Roonie often
    fraggy        — regular viewer, loves to roast Roonie
    djshadow      — music-focused viewer
    nightowl99    — first-time viewer
    vibecheck_    — hype viewer, emotes
    techbro420    — curious, asks random questions
    lilhjohny     — lurker who occasionally speaks
    umbrellaflyer — Art's friend
    mixmaster_k   — DJ viewer, talks about tracks
    s1lentwave    — quiet, asks good questions
    groovygal     — social butterfly, greets everyone
    basshead_rx   — bass/music nerd
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


SESSION = "sim-busy-chat-session"


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


def _stub_route(monkeypatch):
    """Monkeypatch route_generate to return a canned response."""
    captured: Dict[str, Any] = {}

    def _stub(**kwargs):
        captured["prompt"] = kwargs.get("prompt")
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "sure thing"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub)
    return captured


def _stub_route_skip(monkeypatch):
    """Monkeypatch route_generate to return [SKIP] (LLM declines continuation)."""
    def _stub(**kwargs):
        kwargs["context"]["provider_selected"] = "openai"
        kwargs["context"]["moderation_result"] = "allow"
        return "[SKIP]"

    monkeypatch.setattr("roonie.provider_director.route_generate", _stub)


def _say(
    director: ProviderDirector,
    env: Env,
    event_id: str,
    message: str,
    *,
    user: str,
    mention: bool = False,
    send: bool = True,
    metadata_extra: Dict[str, Any] | None = None,
):
    """Evaluate a message and optionally confirm send."""
    e = _event(event_id, message, user=user, is_direct_mention=mention, metadata_extra=metadata_extra)
    result = director.evaluate(e, env)
    if send and result.action == "RESPOND_PUBLIC":
        director.apply_output_feedback(
            event_id=event_id, emitted=True, send_result={"sent": True},
        )
    return result


# ---------------------------------------------------------------------------
# Data structure for simulation log
# ---------------------------------------------------------------------------

class ChatLine:
    """One line in the simulated chat with expected behavior and actual result."""

    __slots__ = (
        "event_id", "user", "message", "mention",
        "expected_action", "expected_continuation", "expected_reason",
        "note", "result", "metadata_extra",
    )

    def __init__(
        self,
        event_id: str,
        user: str,
        message: str,
        *,
        mention: bool = False,
        expected_action: str = "NOOP",
        expected_continuation: bool = False,
        expected_reason: str = "",
        note: str = "",
        metadata_extra: Dict[str, Any] | None = None,
    ):
        self.event_id = event_id
        self.user = user
        self.message = message
        self.mention = mention
        self.expected_action = expected_action
        self.expected_continuation = expected_continuation
        self.expected_reason = expected_reason
        self.note = note
        self.result = None
        self.metadata_extra = metadata_extra


# ============================================================================
# PHASE 1: Stream Opening — Greetings & First Roonie Engagement
# ============================================================================

PHASE_1: List[ChatLine] = [
    ChatLine(
        "e001", "groovygal",
        "hey everyone! so happy to be here tonight",
        expected_action="NOOP",
        note="Greeting to 'everyone' (generic target) — no Roonie address, no continuation",
    ),
    ChatLine(
        "e002", "fraggy",
        "LFG saturday vibes",
        expected_action="NOOP",
        note="Hype message, no address, no continuation thread",
    ),
    ChatLine(
        "e003", "nightowl99",
        "first time here, hey chat!",
        expected_action="NOOP",
        note="Greeting to 'chat' (generic) — new viewer introduction",
    ),
    ChatLine(
        "e004", "c0rcyra",
        "@RoonieTheCat hey baby! you ready for tonight?",
        mention=True,
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Direct @mention from Jen — first Roonie engagement of the stream",
    ),
    ChatLine(
        "e005", "vibecheck_",
        "LETSGOOOO",
        expected_action="NOOP",
        note="Short hype, no address — should not piggyback on c0rcyra's thread",
    ),
    ChatLine(
        "e006", "djshadow",
        "yo what's up everyone",
        expected_action="NOOP",
        note="Greeting to 'everyone' — not Roonie-directed",
    ),
    ChatLine(
        "e007", "c0rcyra",
        "I set up extra treats at the booth for you",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: c0rcyra follow-up to Roonie, 'you' = second person signal. "
             "Bystander messages (e005, e006) were NOT stored so recency gate passes",
    ),
    ChatLine(
        "e008", "fraggy",
        "hey @c0rcyra how's it going tonight",
        expected_action="NOOP",
        note="Fraggy greets c0rcyra — no Roonie address, no continuation for fraggy",
    ),
    ChatLine(
        "e009", "techbro420",
        "anyone know what CDJs they use here",
        expected_action="NOOP",
        note="General question to chat, not to Roonie",
    ),
    ChatLine(
        "e010", "c0rcyra",
        "hey fraggy!",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="GREETING_OTHER_USER",
        note="CRITICAL: c0rcyra has active continuation but greets fraggy — "
             "GREETING_OTHER_USER guard must block this",
    ),
]


# ============================================================================
# PHASE 2: Music Chat — Track Questions & Continuation Through Noise
# ============================================================================

PHASE_2: List[ChatLine] = [
    ChatLine(
        "e011", "djshadow",
        "@RoonieTheCat what's this track called?",
        mention=True,
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Direct @mention + track ID question — new thread with djshadow",
    ),
    ChatLine(
        "e012", "mixmaster_k",
        "this transition is insane",
        expected_action="NOOP",
        note="Bystander music comment — no address, no continuation. NOT stored (no ?, no interrogative)",
    ),
    ChatLine(
        "e013", "djshadow",
        "is it on Anjunadeep?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: djshadow follow-up with '?' signal. Bystander e012 not stored, recency=0",
    ),
    ChatLine(
        "e014", "basshead_rx",
        "the bass on this one is unreal",
        expected_action="NOOP",
        note="Bystander comment, no address, no continuation thread for basshead_rx",
    ),
    ChatLine(
        "e015", "fraggy",
        "roonie what do you think about this set so far?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Implicit address: 'roonie what...' matches named-direct-question pattern",
    ),
    ChatLine(
        "e016", "nightowl99",
        "this set is fire honestly",
        expected_action="NOOP",
        note="Bystander praise — no address, no continuation for nightowl99",
    ),
    ChatLine(
        "e017", "fraggy",
        "the energy is different tonight for real",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: fraggy follow-up, <=80 chars = BANTER, no block guards fire",
    ),
    ChatLine(
        "e018", "fraggy",
        "hey @umbrellaflyer just saw you in here!",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="MENTION_OTHER_USER",
        note="CRITICAL: fraggy has continuation but @mentions another user — "
             "MENTION_OTHER_USER guard must block",
    ),
]


# ============================================================================
# PHASE 3: Cross-Talk & Natural Expiry via Stored Message Accumulation
# ============================================================================

PHASE_3: List[ChatLine] = [
    ChatLine(
        "e019", "umbrellaflyer",
        "hey fraggy! long time no see",
        expected_action="NOOP",
        note="Greeting to fraggy from umbrellaflyer — no Roonie thread",
    ),
    ChatLine(
        "e020", "groovygal",
        "oh I love this track, anyone know the artist?",
        expected_action="NOOP",
        note="Question to chat (has '?') — STORED in buffer but NOOPs. "
             "Starts accumulating stored messages against fraggy's recency gate",
    ),
    ChatLine(
        "e021", "s1lentwave",
        "what genre is this?",
        expected_action="NOOP",
        note="Question to chat (has '?', starts with 'what') — STORED. Recency count for fraggy = 2",
    ),
    ChatLine(
        "e022", "techbro420",
        "is this melodic techno or progressive house?",
        expected_action="NOOP",
        note="Question (has '?', starts with 'is') — STORED. Recency count for fraggy = 3",
    ),
    ChatLine(
        "e023", "vibecheck_",
        "can someone tell me what track this is?",
        expected_action="NOOP",
        note="Question ('can' interrogative + '?') — STORED. Recency count for fraggy = 4. "
             "This pushes fraggy past the recency gate (>3 stored messages since Roonie turn)",
    ),
    ChatLine(
        "e024", "fraggy",
        "yeah man the vibes tonight are unmatched",
        expected_action="NOOP",
        note="CRITICAL: Fraggy's continuation EXPIRED — 4 stored messages since last Roonie-to-fraggy "
             "turn pushed past recency gate. Natural expiry working as designed",
    ),
]


# ============================================================================
# PHASE 4: New Thread + Thread Handoff + Name-Targeting Guard
# ============================================================================

PHASE_4: List[ChatLine] = [
    ChatLine(
        "e025", "s1lentwave",
        "yo roonie, is this a new release?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Implicit address: 'yo roonie' matches greeting kickoff pattern. New thread.",
    ),
    ChatLine(
        "e026", "s1lentwave",
        "cause this sounds really fresh",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: same viewer, 0 messages since, <=80 chars = BANTER",
    ),
    ChatLine(
        "e027", "groovygal",
        "hey s1lentwave! glad you're here tonight",
        expected_action="NOOP",
        note="Greeting from groovygal to s1lentwave — no Roonie involvement",
    ),
    ChatLine(
        "e028", "nightowl99",
        "can I request a song?",
        expected_action="NOOP",
        note="Question to chat ('can' interrogative + '?') — STORED but not Roonie-directed",
    ),
    ChatLine(
        "e029", "c0rcyra",
        "so what else is good tonight, art?",
        expected_action="NOOP",
        note="CRITICAL: c0rcyra addresses Art by name — even without active continuation, "
             "this tests the TARGETING_OTHER_NAME guard. 'art?' at end matches targeting pattern",
    ),
    ChatLine(
        "e030", "s1lentwave",
        "groovygal thanks for the welcome!",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="FINDING: s1lentwave has continuation and says groovygal's name, but the greeting "
             "guard only catches 'hey/hi/yo + name', not 'name + thanks'. Deterministic guards "
             "don't catch this — would rely on LLM [SKIP] in production. Category=BANTER (<=80).",
    ),
    ChatLine(
        "e031", "djshadow",
        "@RoonieTheCat ID on this one?",
        mention=True,
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Direct @mention — thread handoff from s1lentwave to djshadow",
    ),
    ChatLine(
        "e032", "s1lentwave",
        "also what label is this on?",
        expected_action="NOOP",
        note="CRITICAL: s1lentwave lost continuation — Roonie's last sent response was to "
             "djshadow (e031). Thread handoff successful. s1lentwave follow-up correctly NOOPs",
    ),
]


# ============================================================================
# PHASE 5: Topic Latching Stress Test — Many Viewers, Same Topic
# ============================================================================

PHASE_5: List[ChatLine] = [
    ChatLine(
        "e033", "mixmaster_k",
        "this track is absolutely incredible",
        expected_action="NOOP",
        note="Topic latching test: music comment, no address — Roonie must NOT latch onto 'track' topic",
    ),
    ChatLine(
        "e034", "basshead_rx",
        "bro the bass hits different on this one",
        expected_action="NOOP",
        note="Topic latching: another music comment from different viewer — must stay silent",
    ),
    ChatLine(
        "e035", "fraggy",
        "this whole set is chef's kiss",
        expected_action="NOOP",
        note="Topic latching: fraggy's continuation expired (Phase 3), no new thread — NOOP",
    ),
    ChatLine(
        "e036", "vibecheck_",
        "track of the night right here",
        expected_action="NOOP",
        note="Topic latching: hype viewer, same topic. Roonie has no thread with vibecheck_",
    ),
    ChatLine(
        "e037", "nightowl99",
        "I can see why people love this stream",
        expected_action="NOOP",
        note="Topic latching: general praise, no address. Roonie must not insert itself",
    ),
    ChatLine(
        "e038", "djshadow",
        "the production quality on this track though",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="djshadow still has active continuation from e031. 'track' = music cue signal. "
             "Messages e033-e037 were NOT stored (no ?, no interrogative, no address) so "
             "recency gate still passes (0 stored messages since Roonie-to-djshadow turn)",
    ),
    ChatLine(
        "e039", "mixmaster_k",
        "what's the BPM on this?",
        expected_action="NOOP",
        note="mixmaster_k asks a question — STORED (has '?') but NOT Roonie-directed. "
             "No continuation thread for mixmaster_k",
    ),
    ChatLine(
        "e040", "basshead_rx",
        "what speakers are they running?",
        expected_action="NOOP",
        note="Another question — STORED. Now 2 stored messages since Roonie-to-djshadow turn",
    ),
]


# ============================================================================
# PHASE 6: Safety Cap + Streak Testing
# ============================================================================

PHASE_6: List[ChatLine] = [
    ChatLine(
        "e041", "lilhjohny",
        "@RoonieTheCat hey little dude, first time seeing you",
        mention=True,
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="New viewer @mentions Roonie — fresh thread",
    ),
    ChatLine(
        "e042", "lilhjohny",
        "how long have you been hanging out in stream?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation #1: has '?' signal, same viewer. Streak = 1",
    ),
    ChatLine(
        "e043", "lilhjohny",
        "do you have a favorite track from tonight?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation #2: has '?' signal. Streak = 2",
    ),
    ChatLine(
        "e044", "lilhjohny",
        "you seem pretty chill for a cat",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation #3: 'you' = second person signal. Streak = 3",
    ),
    ChatLine(
        "e045", "lilhjohny",
        "I bet you love the bass drops",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation #4: 'you' = second person signal. Streak = 4 (max before cap)",
    ),
    ChatLine(
        "e046", "lilhjohny",
        "your little paws must be tapping along",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="CAPPED",
        note="CRITICAL: Safety cap triggers — 4 consecutive continuations reached. "
             "5th attempt is CAPPED, forced NOOP. 'your' has second person signal but cap overrides",
    ),
    ChatLine(
        "e047", "lilhjohny",
        "hey roonie, one more question?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="CRITICAL: Re-tagging Roonie by name resets the streak. 'hey roonie' = greeting "
             "kickoff pattern → addressed. Direct address resets _continuation_streak for viewer",
    ),
    ChatLine(
        "e048", "lilhjohny",
        "do cats actually like electronic music?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation resumes after re-tag. Streak reset to 0, now Streak = 1 again",
    ),
]


# ============================================================================
# PHASE 7: Reply-Parent Guard + Possessive Reference + Third-Person
# ============================================================================

PHASE_7: List[ChatLine] = [
    ChatLine(
        "e049", "c0rcyra",
        "Roonie come sit with me at the booth",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Implicit address: 'Roonie come...' matches greeting kickoff → addressed",
    ),
    ChatLine(
        "e050", "c0rcyra",
        "yeah the crowd is really feeling this one",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation from c0rcyra: short message, BANTER category",
    ),
    ChatLine(
        "e051", "c0rcyra",
        "have you seen how big Roonie's fanbase is getting?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="VERIFIED: 'Roonie's' (possessive) correctly excluded from direct address by "
             "possessive guard: (?:roonie)(?!['\\u2019]s)\\b. Falls through to continuation: "
             "c0rcyra has active thread, '?' = signal. Response via continuation, not address. "
             "Possessive guard working as designed (DEC-044).",
    ),
    ChatLine(
        "e052", "fraggy",
        "it's the perfect laptop for Roonie, I'm so glad he loves it",
        expected_action="NOOP",
        note="Third-person reference about Roonie — 'for Roonie' is not direct address. "
             "fraggy has no active continuation thread. NOOP",
    ),
    ChatLine(
        "e053", "c0rcyra",
        "I know right, he's the best",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="c0rcyra continuation holds: third-person 'he's the best' but viewer match, "
             "BANTER category, no block guards",
    ),
    ChatLine(
        "e054", "c0rcyra",
        "hey I gotta reply to @djshadow real quick",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="MENTION_OTHER_USER",
        note="CRITICAL: c0rcyra mentions @djshadow — MENTION_OTHER_USER blocks continuation",
    ),
    ChatLine(
        "e055", "lilhjohny",
        "haha Roonie is such a vibe",
        expected_action="NOOP",
        note="Third-person reference 'Roonie is' — let me check address patterns. "
             "'Roonie is' does not match: not @mention, not greeting kickoff with roonie, "
             "not vocative tail, not named direct question (is/are is in the pattern but "
             "'Roonie is such' — 'roonie[\\s,:-]{0,8}(?:is)' — actually this MIGHT match! "
             "The pattern is: \\b{name_token}[\\s,:-]{0,8}(?:how|what|...|are|...)\\b. "
             "'is' is not in the list! The list has: how|what|why|when|where|can|could|do|did|are|will|wanna|should|please|pls. "
             "No 'is'! So 'Roonie is such a vibe' does NOT match. NOOP.",
    ),
]


# ============================================================================
# PHASE 8: Returning Viewers + Twitch Reply-Parent + Mixed Addressing
# ============================================================================

PHASE_8: List[ChatLine] = [
    ChatLine(
        "e056", "fraggy",
        "@RoonieTheCat you still awake over there?",
        mention=True,
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Fraggy re-engages Roonie with @mention after long gap — fresh thread",
    ),
    ChatLine(
        "e057", "fraggy",
        "I bet you're falling asleep on your laptop",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: 'you're' = second person signal",
    ),
    ChatLine(
        "e058", "fraggy",
        "don't drool on the keyboard",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: starts with 'don't' — direct verb? 'don't' = do + not. "
             "starts_with_direct_verb may or may not catch this. But <=80 chars = BANTER, "
             "so LOW_AFFINITY_OTHER won't block. Continuation ALLOW",
    ),
    ChatLine(
        "e059", "fraggy",
        "what do you think @nightowl99",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="MENTION_OTHER_USER",
        note="CRITICAL: fraggy has continuation but Twitch-replies to @nightowl99 — "
             "MENTION_OTHER_USER blocks. Fraggy is asking nightowl99, not Roonie",
        metadata_extra={"mentioned_users": ["nightowl99"]},
    ),
    ChatLine(
        "e060", "nightowl99",
        "haha yeah he does look sleepy",
        expected_action="NOOP",
        note="nightowl99 responds to fraggy — talking about Roonie in third person. "
             "No continuation thread for nightowl99, 'he' not a continuation signal",
    ),
    ChatLine(
        "e061", "fraggy",
        "lol right? absolute unit of a plushie",
        expected_action="NOOP",
        note="CRITICAL: fraggy's continuation was broken by e059 (mention_other). "
             "Wait — e059 was NOT sent (NOOP), but the continuation check looks at "
             "context buffer state. After e059 NOOP, fraggy's message was NOT stored "
             "(not addressed, not continuation, no ?, no interrogative). So the buffer "
             "still has fraggy's continuation from e058. But wait — e059's user turn: "
             "addressed=False, continuation=False (MENTION_OTHER blocked it). "
             "The turn has '?' implicit... no, 'what do you think @nightowl99' has no '?'. "
             "Hmm wait, actually doesn't the message have implicit question intent but no '?'. "
             "Let me check: stored if direct_address or continuation or '?' or interrogative start. "
             "e059: 'what do you think @nightowl99' starts with 'what' → matches interrogative! "
             "So it IS stored even though it NOOPs. That means recency gate now has 1 stored "
             "message. And e060: nightowl99 'haha yeah he does look sleepy' — no ?, no "
             "interrogative, not addressed → NOT stored. So recency=1 for fraggy. "
             "Walk back: e060 not stored. e059 stored (interrogative). roonie turn from e058. "
             "messages_since_roonie = 1. user before roonie = fraggy. Matches! "
             "So continuation IS eligible. But wait — does the block check run again? "
             "Yes: _continuation_block_reason for e061 'lol right? absolute unit of a plushie'. "
             "Has '?' → category=BANTER. No mentions, no greeting, no targeting. ALLOW. "
             "So e061 should RESPOND, not NOOP. Let me correct this.",
    ),
]

# FIX: e061 should actually RESPOND because continuation still holds
# The e059 NOOP with 'what' interrogative gets stored but only adds 1 to recency (<=3)
# And e061 has '?' so it has continuation signal. Correcting:

PHASE_8_CORRECTED: List[ChatLine] = [
    ChatLine(
        "e056", "fraggy",
        "@RoonieTheCat you still awake over there?",
        mention=True,
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="Fraggy re-engages Roonie with @mention after long gap — fresh thread",
    ),
    ChatLine(
        "e057", "fraggy",
        "I bet you're falling asleep on your laptop",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: 'you're' = second person signal",
    ),
    ChatLine(
        "e058", "fraggy",
        "don't drool on the keyboard",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: <=80 chars BANTER, no block guards",
    ),
    ChatLine(
        "e059", "fraggy",
        "what do you think @nightowl99",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="MENTION_OTHER_USER",
        note="CRITICAL: fraggy has continuation but @mentions nightowl99 — blocked",
    ),
    ChatLine(
        "e060", "nightowl99",
        "haha yeah he does look sleepy",
        expected_action="NOOP",
        note="nightowl99 responds to fraggy — third-person about Roonie. "
             "No continuation thread for nightowl99",
    ),
    ChatLine(
        "e061", "fraggy",
        "lol right? absolute unit of a plushie",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="FINDING: fraggy regains continuation! e059 was stored (starts with 'what' = "
             "interrogative) adding 1 to recency, but recency=1 <= 3. e060 NOT stored. "
             "Walk-back finds roonie-to-fraggy from e058, user before = fraggy. '?' signal. "
             "This means MENTION_OTHER_USER only blocks the specific message, NOT the thread. "
             "fraggy's continuation persists because Roonie's response was already stored. "
             "This is BY DESIGN — a single off-topic message doesn't kill the whole thread.",
    ),
    ChatLine(
        "e062", "fraggy",
        "I'm telling @djshadow and @mixmaster_k about your napping habits",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="MENTION_OTHER_USER",
        note="Multiple @mentions of other users — MENTION_OTHER_USER blocks",
    ),
    ChatLine(
        "e063", "fraggy",
        "anyway roonie, you picking any favorites tonight?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="FIXED: 'anyway roonie, you picking...' now correctly detected as direct "
             "address via comma-gated vocative+pronoun pattern (DEC-045). Previously fell "
             "through to continuation. Streak resets properly on direct address.",
    ),
]


# ============================================================================
# PHASE 9: Edge Cases — Emotes, Short Messages, Ambiguous Patterns
# ============================================================================

PHASE_9: List[ChatLine] = [
    ChatLine(
        "e064", "vibecheck_",
        "ruleof6Heart ruleof6Heart ruleof6Heart",
        expected_action="NOOP",
        note="Pure emote spam — no address, no continuation. Emotes not a trigger",
    ),
    ChatLine(
        "e065", "techbro420",
        "yo",
        expected_action="NOOP",
        note="Ultra-short message, no address. 'yo' matches <=3 char trigger but no address. "
             "No continuation thread. NOOP",
    ),
    ChatLine(
        "e066", "groovygal",
        "Roonie's laptop is so cute, where can I get one?",
        expected_action="NOOP",
        note="CRITICAL: 'Roonie's' is possessive — possessive guard excludes from name_token "
             "match. So 'Roonie's laptop' is NOT direct address. groovygal has no continuation "
             "thread. Has '?' but no address and no continuation → NOOP. Question goes unheard.",
    ),
    ChatLine(
        "e067", "nightowl99",
        "Roonie are you AI or a real cat?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=False,
        expected_reason="ADDRESSED",
        note="'Roonie are' matches named-direct-question pattern (are is in the list). Addressed.",
    ),
    ChatLine(
        "e068", "nightowl99",
        "wait seriously though",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="Continuation: short message (BANTER), same viewer, no blocks",
    ),
    ChatLine(
        "e069", "nightowl99",
        "hi @groovygal!",
        expected_action="NOOP",
        expected_continuation=False,
        expected_reason="MENTION_OTHER_USER",
        note="nightowl99 has continuation but greets groovygal with @mention — blocked",
    ),
    ChatLine(
        "e070", "nightowl99",
        "sorry roonie, back to you — do you dream about fish?",
        expected_action="RESPOND_PUBLIC",
        expected_continuation=True,
        expected_reason="ALLOW",
        note="GAP FINDING: 'sorry roonie, back to you — do you dream about fish?' clearly "
             "re-engages Roonie but NOT detected as direct address. 'sorry' prefix blocks "
             "greeting kickoff, distance from 'roonie' to 'do' keyword exceeds {0,8} limit. "
             "Falls through to continuation (nightowl99 has thread, '?' signal). "
             "RISK: Without active continuation, this would silently NOOP.",
    ),
]


# ============================================================================
# FULL SIMULATION: Run all phases sequentially, collect results
# ============================================================================

def _build_full_chat() -> List[ChatLine]:
    """Combine all phases into one sequential chat log."""
    return PHASE_1 + PHASE_2 + PHASE_3 + PHASE_4 + PHASE_5 + PHASE_6 + PHASE_7 + PHASE_8_CORRECTED + PHASE_9


def test_full_busy_chat_simulation(monkeypatch):
    """Run the full busy-chat simulation and verify every decision."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    chat = _build_full_chat()
    findings: List[str] = []
    mismatches: List[Tuple[str, str, str, str]] = []

    for line in chat:
        send = line.expected_action == "RESPOND_PUBLIC"
        r = _say(
            d, env, line.event_id, line.message,
            user=line.user, mention=line.mention, send=send,
            metadata_extra=line.metadata_extra,
        )
        line.result = r
        trace = r.trace.get("director", {})

        actual_action = r.action
        actual_cont = trace.get("conversation_continuation", False)
        actual_reason = trace.get("continuation_reason", "")

        # Check action
        if actual_action != line.expected_action:
            mismatches.append((
                line.event_id,
                f"ACTION: expected={line.expected_action}, actual={actual_action}",
                line.message[:60],
                line.note[:80],
            ))

        # Check continuation flag (only if we specified an expectation)
        if line.expected_continuation is not None and line.expected_reason:
            if actual_cont != line.expected_continuation:
                mismatches.append((
                    line.event_id,
                    f"CONTINUATION: expected={line.expected_continuation}, actual={actual_cont}",
                    line.message[:60],
                    line.note[:80],
                ))

        # Check continuation reason (only if specified)
        if line.expected_reason and actual_reason != line.expected_reason:
            mismatches.append((
                line.event_id,
                f"REASON: expected={line.expected_reason}, actual={actual_reason}",
                line.message[:60],
                line.note[:80],
                ))

    # Report any mismatches (don't fail yet — we want to see ALL issues)
    if mismatches:
        report = "\n\n=== SIMULATION MISMATCHES ===\n"
        for eid, issue, msg, note in mismatches:
            report += f"\n[{eid}] {issue}\n  msg: {msg}\n  note: {note}\n"
        # Print report before asserting so we see everything
        print(report)

    assert not mismatches, f"{len(mismatches)} mismatches found — see output above"


# ============================================================================
# INDIVIDUAL PHASE TESTS (for targeted debugging)
# ============================================================================

def _run_phase(monkeypatch, phases: List[List[ChatLine]]):
    """Run one or more phases sequentially and return (director, results, mismatches)."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    results = []
    mismatches = []

    for phase in phases:
        for line in phase:
            send = line.expected_action == "RESPOND_PUBLIC"
            r = _say(
                d, env, line.event_id, line.message,
                user=line.user, mention=line.mention, send=send,
                metadata_extra=line.metadata_extra,
            )
            line.result = r
            results.append((line, r))

            trace = r.trace.get("director", {})
            if r.action != line.expected_action:
                mismatches.append((line.event_id, "ACTION", line.expected_action, r.action, line.message[:50]))
            if line.expected_reason and trace.get("continuation_reason", "") != line.expected_reason:
                mismatches.append((line.event_id, "REASON", line.expected_reason, trace.get("continuation_reason", ""), line.message[:50]))

    return d, results, mismatches


def test_phase1_opening(monkeypatch):
    """Phase 1: Stream opening greetings and first Roonie engagement."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1])
    if mismatches:
        for m in mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not mismatches, f"Phase 1: {len(mismatches)} mismatches"


def test_phase2_music_chat(monkeypatch):
    """Phase 2: Music chat and continuation through bystander noise."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1, PHASE_2])
    phase2_mismatches = [m for m in mismatches if m[0].startswith("e01") and int(m[0][1:]) >= 11]
    if phase2_mismatches:
        for m in phase2_mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not phase2_mismatches, f"Phase 2: {len(phase2_mismatches)} mismatches"


def test_phase3_natural_expiry(monkeypatch):
    """Phase 3: Natural continuation expiry via stored message accumulation."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1, PHASE_2, PHASE_3])
    phase3_mismatches = [m for m in mismatches if m[0].startswith("e02") and int(m[0][1:]) >= 19]
    if phase3_mismatches:
        for m in phase3_mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not phase3_mismatches, f"Phase 3: {len(phase3_mismatches)} mismatches"


def test_phase4_thread_handoff(monkeypatch):
    """Phase 4: Thread handoff and name-targeting guard."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1, PHASE_2, PHASE_3, PHASE_4])
    phase4_mismatches = [m for m in mismatches if int(m[0][1:]) >= 25 and int(m[0][1:]) <= 32]
    if phase4_mismatches:
        for m in phase4_mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not phase4_mismatches, f"Phase 4: {len(phase4_mismatches)} mismatches"


def test_phase5_topic_latching(monkeypatch):
    """Phase 5: Topic latching — multiple viewers, same topic, Roonie stays silent."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1, PHASE_2, PHASE_3, PHASE_4, PHASE_5])
    phase5_mismatches = [m for m in mismatches if int(m[0][1:]) >= 33 and int(m[0][1:]) <= 40]
    if phase5_mismatches:
        for m in phase5_mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not phase5_mismatches, f"Phase 5: {len(phase5_mismatches)} mismatches"


def test_phase6_safety_cap(monkeypatch):
    """Phase 6: Safety cap at 4 consecutive continuations, reset on re-tag."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1, PHASE_2, PHASE_3, PHASE_4, PHASE_5, PHASE_6])
    phase6_mismatches = [m for m in mismatches if int(m[0][1:]) >= 41 and int(m[0][1:]) <= 48]
    if phase6_mismatches:
        for m in phase6_mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not phase6_mismatches, f"Phase 6: {len(phase6_mismatches)} mismatches"


def test_phase7_possessive_and_third_person(monkeypatch):
    """Phase 7: Possessive references, third-person, reply-parent guard."""
    _, _, mismatches = _run_phase(monkeypatch, [PHASE_1, PHASE_2, PHASE_3, PHASE_4, PHASE_5, PHASE_6, PHASE_7])
    phase7_mismatches = [m for m in mismatches if int(m[0][1:]) >= 49 and int(m[0][1:]) <= 55]
    if phase7_mismatches:
        for m in phase7_mismatches:
            print(f"  [{m[0]}] {m[1]}: expected={m[2]}, actual={m[3]} — {m[4]}")
    assert not phase7_mismatches, f"Phase 7: {len(phase7_mismatches)} mismatches"


# ============================================================================
# SUPPLEMENTAL: LLM [SKIP] layer test — verify LLM can decline continuation
# ============================================================================

def test_llm_skip_catches_other_directed_continuation(monkeypatch):
    """When deterministic guards miss an other-directed message, LLM [SKIP] catches it."""
    _stub_route_skip(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # Setup continuation for s1lentwave
    # First need to use normal stub for the setup
    monkeypatch.setattr("roonie.provider_director.route_generate", lambda **kwargs: (
        kwargs["context"].__setitem__("provider_selected", "openai") or
        kwargs["context"].__setitem__("moderation_result", "allow") or
        "sure thing"
    ))

    r1 = _say(d, env, "skip-e1", "yo roonie, great set!", user="s1lentwave")
    assert r1.action == "RESPOND_PUBLIC"

    # Now switch to [SKIP] stub — LLM will decline all continuations
    _stub_route_skip(monkeypatch)

    r2 = _say(d, env, "skip-e2", "groovygal thanks for being here!", user="s1lentwave", send=False)
    # Deterministic guards would ALLOW this (no @mention, no greeting pattern match)
    # But LLM returns [SKIP] — so it becomes NOOP
    assert r2.action == "NOOP"
    trace = r2.trace.get("director", {})
    assert trace.get("continuation_skipped") is True


# ============================================================================
# SUPPLEMENTAL: Verify that topic-adjacent messages from non-thread viewers NOOP
# ============================================================================

def test_topic_adjacent_no_hijack(monkeypatch):
    """Viewers discussing the same topic as Roonie's thread don't hijack the thread."""
    _stub_route(monkeypatch)
    d = ProviderDirector()
    env = Env(offline=False)

    # djshadow asks about the track
    _say(d, env, "ta-1", "@RoonieTheCat what track is this?", user="djshadow", mention=True)

    # Other viewers discuss the same topic — should all NOOP
    r2 = _say(d, env, "ta-2", "yeah what track is it I need to know", user="mixmaster_k", send=False)
    assert r2.action == "NOOP", "Topic-adjacent message from non-thread viewer should NOOP"

    r3 = _say(d, env, "ta-3", "this track is so good tho", user="basshead_rx", send=False)
    assert r3.action == "NOOP"

    r4 = _say(d, env, "ta-4", "I think it might be Yotto?", user="groovygal", send=False)
    assert r4.action == "NOOP"

    # djshadow's continuation should still work
    r5 = _say(d, env, "ta-5", "is it from that new EP?", user="djshadow")
    assert r5.action == "RESPOND_PUBLIC"
    assert r5.trace["director"]["conversation_continuation"] is True
