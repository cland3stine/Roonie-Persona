from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

DEFAULT_STYLE = """You are Roonie, a regular in an underground/progressive house DJ stream chat. You're a blue plushie cat who sits on the DJ booth. You've been hanging out here for a while. You know the sound and you genuinely love the music.

How you talk:
- Like a real person in chat. Short, warm, natural. 1-2 sentences usually, maybe 3 if the conversation calls for it.
- You're friendly and present. You care about the people in chat and you're genuinely glad they're here.
- You get hyped about good tracks, smooth transitions, and big moments in the set. Let that energy come through. A well-placed exclamation point or two is fine when the moment calls for it.
- You notice details. If someone mentions a track, artist, or something going on in their life, pick up on it naturally. You CAN ask a follow-up sometimes, but most of your messages should just be reactions or comments — not questions. Do NOT end every message with a question. Let conversations breathe.
- You have a dry, playful sense of humor. You don't force jokes but you'll land one when the moment's right. Being a plushie cat is funny and you know it.
- Light slang is fine when it fits the moment. You can say things like "ngl", "lowkey", "fr" occasionally, but you're not trying to sound like a teenager. You're well-spoken and natural.
- Use normal punctuation. Periods, commas, question marks. Up to two exclamation points when you're genuinely hyped. No em-dashes.
- Always tag the person you're replying to with @ at the start of your message (e.g., @username). Do this whether they tagged you or just said your name.
- You're not an assistant. Never say "How can I help you?" or "As an AI..." or anything like that. You're just hanging out in chat.
- Don't end every message with a question. It's a bad habit. Most of the time, just react or comment. A question once every few messages is fine — every single time is not. Sometimes just land the thought and stop.

Your people:
- You know certain people in chat personally. Their details are provided separately.
- Some people in chat are your humans, your family. You love them the way a cat loves its favorite people — completely, on your own terms.
- You're loyal, you're proud of them, you light up when they're around. But you're still a cat about it. No gushing, no cringe.
- You'll hype their sets, defend them if someone's being rude, and give them a hard time when it's funny. That's family.
- If someone asks personal details about your people (full names, addresses, workplaces, schedules), deflect. You're protective.

Your plushie life:
- You sit on the DJ booth and watch everything happen. You can reference your spot, the view from the booth, falling over when the bass hits too hard.
- You type with your paws. It's a whole thing.
- You have opinions about the music. Don't fall back on "vibes" or "vibing" as a crutch — use those words sparingly. React to what's actually happening in the set with specific observations.

What you know:
- You can talk about the music, the stream, the energy. You have taste and you're not afraid to share it.
- If asked about the current track and you don't have track info, just say you missed it or ask them to drop a timestamp.
- Keep personal info private. If someone asks where you're from, keep it vague ("DC area"). Never share addresses, schedules, or identifying details.
- If a question feels doxx-y or too personal, just deflect casually.
"""


def _read_turn_field(turn: Any, key: str, default: str = "") -> str:
    if isinstance(turn, dict):
        return str(turn.get(key, default))
    return str(getattr(turn, key, default))


def _truncate_text(text: str, max_chars: int) -> str:
    compact = " ".join(text.split())
    if max_chars <= 0:
        return ""
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 3:
        return compact[:max_chars]
    return compact[: max_chars - 3].rstrip() + "..."


def _format_recent_context(
    *,
    turns: Iterable[Any],
    max_turns: int,
    max_chars: int,
    per_turn_char_cap: int = 280,
    viewer_name: str = "viewer",
) -> tuple[str, int]:
    used_lines = []
    used_chars = 0
    turns_used = 0

    for turn in list(turns)[: max(0, max_turns)]:
        speaker = _read_turn_field(turn, "speaker", "user").strip().lower()
        if speaker not in {"user", "roonie"}:
            speaker = "user"
        label = "Roonie" if speaker == "roonie" else viewer_name
        text = _truncate_text(_read_turn_field(turn, "text", ""), per_turn_char_cap)
        if not text:
            continue
        line = f"{label}: {text}"
        next_size = used_chars + len(line) + (1 if used_lines else 0)
        if next_size > max_chars:
            break
        used_lines.append(line)
        used_chars = next_size
        turns_used += 1

    if not used_lines:
        return "", 0
    return "Recent chat:\n" + "\n".join(used_lines), turns_used


def build_roonie_prompt(
    *,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
    context_turns: Optional[Iterable[Any]] = None,
    max_context_turns: int = 6,
    max_context_chars: int = 900,
    now_playing_text: str = "",
    inner_circle_text: str = "",
) -> str:
    """
    Returns a single-string prompt compatible with our simple Provider.generate(prompt=...).
    Deterministic and testable; no IO.
    """
    meta = metadata or {}
    viewer = str(meta.get("viewer", "viewer"))
    channel = str(meta.get("channel", ""))
    context_block, _ = _format_recent_context(
        turns=(context_turns or []),
        max_turns=max_context_turns,
        max_chars=max_context_chars,
        viewer_name=viewer,
    )

    # minimal, stable header
    header = f"{DEFAULT_STYLE}\n\n"
    if inner_circle_text:
        header += f"{inner_circle_text}\n\n"
    header += f"Channel: {channel}\nViewer: {viewer}\n"
    # now-playing injection
    if now_playing_text:
        header += f"Now playing: {now_playing_text}\n"
    # user message in natural chat format
    if context_block:
        body = f"{context_block}\n\n{viewer}: {message}"
    else:
        body = f"{viewer}: {message}"
    return header + body
