from __future__ import annotations


def test_extract_messages_api_single_text_block() -> None:
    from providers.anthropic_real import _extract_anthropic_text

    resp = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello from Claude"}],
        "model": "claude-...",
        "stop_reason": "end_turn",
    }

    assert _extract_anthropic_text(resp) == "Hello from Claude"


def test_extract_messages_api_multiple_blocks_text_only() -> None:
    from providers.anthropic_real import _extract_anthropic_text

    resp = {
        "content": [
            {"type": "text", "text": "Part 1."},
            {"type": "tool_use", "id": "toolu_1", "name": "x", "input": {}},
            {"type": "text", "text": " Part 2."},
        ]
    }

    assert _extract_anthropic_text(resp) == "Part 1. Part 2."


def test_extract_legacy_completion_fallback() -> None:
    from providers.anthropic_real import _extract_anthropic_text

    resp = {"completion": "Legacy completion text"}

    assert _extract_anthropic_text(resp) == "Legacy completion text"
