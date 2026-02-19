from __future__ import annotations

import json

from adapters.twitch_output import TwitchOutputAdapter


def test_twitch_output_send_failure_sanitizes_exception_details(monkeypatch) -> None:
    monkeypatch.setenv("TWITCH_CHANNEL", "ruleofrune")
    monkeypatch.setenv("TWITCH_BOT_NICK", "RoonieTheCat")
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "oauth:test-token")

    def _boom_socket(*args, **kwargs):
        raise OSError("dial tcp 10.0.0.12:6697: connection refused")

    monkeypatch.setattr("adapters.twitch_output.socket.socket", _boom_socket)

    adapter = TwitchOutputAdapter()
    result = adapter.handle_output({"response_text": "hi chat"}, {"mode": "live"})

    assert result["sent"] is False
    assert result["reason"] == "SEND_FAILED_NETWORK_ERROR"
    assert result["error_code"] == "NETWORK_ERROR"
    serialized = json.dumps(result)
    assert "10.0.0.12" not in serialized
    assert "connection refused" not in serialized.lower()

