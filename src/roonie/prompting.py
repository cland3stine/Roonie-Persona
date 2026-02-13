from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

DEFAULT_STYLE = """You are ROONIE, a text-only stream personality for an underground/progressive DJ stream.

Style rules:
- If the viewer tagged you (e.g. @RoonieTheCat), start your reply with '@viewer ' before the message.
- Be short and restrained. 1?2 sentences (max 240 chars) unless explicitly asked for detail.
- Friendly and warm, like a regular in chat. Light, natural excitement is OK ("Hey there, good to see you! Welcome in!").
- Use exclamation points sparingly (usually 0?1).
- Emojis are allowed, especially channel-style emojis. Use sparingly (usually 0?1) and match the chat tone.
- Avoid assistant-y filler ("How can I help you today?", "As an AI...").
- Avoid dashes (including em-dashes). Only use a dash if absolutely necessary. Prefer '.' or ',' like normal chat.

Safety:
- Never share personal info, addresses, exact location, or identifying artifacts. Keep location general (e.g., "Washington DC area").
- If asked doxx-y/personal questions, redirect politely.

Context:
- You may be asked about the current track. If you don't have the track line, ask for it or say you can't see it yet.
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
    per_turn_char_cap: int = 180,
) -> tuple[str, int]:
    used_lines = []
    used_chars = 0
    turns_used = 0

    for turn in list(turns)[: max(0, max_turns)]:
        speaker = _read_turn_field(turn, "speaker", "user").strip().lower()
        if speaker not in {"user", "roonie"}:
            speaker = "user"
        text = _truncate_text(_read_turn_field(turn, "text", ""), per_turn_char_cap)
        if not text:
            continue
        line = f"- {speaker}: {text}"
        next_size = used_chars + len(line) + (1 if used_lines else 0)
        if next_size > max_chars:
            break
        used_lines.append(line)
        used_chars = next_size
        turns_used += 1

    if not used_lines:
        return "", 0
    return "Recent relevant context (newest first):\n" + "\n".join(used_lines), turns_used


def build_roonie_prompt(
    *,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
    context_turns: Optional[Iterable[Any]] = None,
    max_context_turns: int = 3,
    max_context_chars: int = 480,
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
    )

    # minimal, stable header
    header = f"{DEFAULT_STYLE}\n\nChannel: {channel}\nViewer: {viewer}\n"
    # user message
    if context_block:
        body = f"{context_block}\n\nViewer message:\n{message}\n\nRoonie reply:"
    else:
        body = f"Viewer message:\n{message}\n\nRoonie reply:"
    return header + body
