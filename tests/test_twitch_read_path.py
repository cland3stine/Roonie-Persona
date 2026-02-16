from __future__ import annotations

from typing import List

import pytest

from twitch import read_path


class _FakeFile:
    def __init__(self, lines: List[bytes]) -> None:
        self._lines = list(lines)
        self.writes: List[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeSocket:
    def __init__(self, file_obj: _FakeFile) -> None:
        self._file = file_obj

    def makefile(self, mode: str, buffering: int = 0) -> _FakeFile:  # noqa: ARG002
        return self._file


class _ErrorFile(_FakeFile):
    def readline(self) -> bytes:
        raise OSError("cannot read from timed out object")


def test_iter_twitch_messages_parses_tagged_privmsg(monkeypatch) -> None:
    fake_file = _FakeFile(
        [
            b":tmi.twitch.tv 001 rooniethecat :Welcome, GLHF!\r\n",
            b"@badge-info=;badges=;display-name=cland3stine :cland3stine!cland3stine@cland3stine.tmi.twitch.tv PRIVMSG #ruleofrune :@RoonieTheCat hey there!\r\n",
            b"PING :tmi.twitch.tv\r\n",
            b"",
        ]
    )

    monkeypatch.setattr(read_path, "_connect_tls", lambda host, port, timeout_s: _FakeSocket(fake_file))

    msgs = list(
        read_path.iter_twitch_messages(
            oauth_token="oauth:abc123",
            nick="rooniethecat",
            channel="ruleofrune",
            timeout_s=2,
            debug=False,
        )
    )

    assert len(msgs) == 1
    assert msgs[0].nick == "cland3stine"
    assert msgs[0].channel == "ruleofrune"
    assert msgs[0].message == "@RoonieTheCat hey there!"

    sent_lines = [line.decode("utf-8").strip() for line in fake_file.writes]
    assert sent_lines[0].startswith("CAP REQ :twitch.tv/tags")
    assert "PASS oauth:abc123" in sent_lines[1]
    assert "NICK rooniethecat" in sent_lines[2]
    assert "JOIN #ruleofrune" in sent_lines[3]
    assert "PONG :tmi.twitch.tv" in sent_lines[-1]


def test_iter_twitch_messages_raises_on_auth_failure_notice(monkeypatch) -> None:
    fake_file = _FakeFile(
        [
            b":tmi.twitch.tv NOTICE * :Login authentication failed\r\n",
            b"",
        ]
    )

    monkeypatch.setattr(read_path, "_connect_tls", lambda host, port, timeout_s: _FakeSocket(fake_file))

    with pytest.raises(RuntimeError, match="auth failed"):
        list(
            read_path.iter_twitch_messages(
                oauth_token="oauth:abc123",
                nick="rooniethecat",
                channel="ruleofrune",
                timeout_s=2,
                debug=False,
            )
        )


def test_iter_twitch_messages_raises_on_reconnect_request(monkeypatch) -> None:
    fake_file = _FakeFile(
        [
            b":tmi.twitch.tv RECONNECT\r\n",
            b"",
        ]
    )

    monkeypatch.setattr(read_path, "_connect_tls", lambda host, port, timeout_s: _FakeSocket(fake_file))

    with pytest.raises(RuntimeError, match="requested reconnect"):
        list(
            read_path.iter_twitch_messages(
                oauth_token="oauth:abc123",
                nick="rooniethecat",
                channel="ruleofrune",
                timeout_s=2,
                debug=False,
            )
        )


def test_iter_twitch_messages_raises_runtime_error_on_stream_oserror(monkeypatch) -> None:
    fake_file = _ErrorFile([])
    monkeypatch.setattr(read_path, "_connect_tls", lambda host, port, timeout_s: _FakeSocket(fake_file))

    with pytest.raises(RuntimeError, match="Twitch IRC read failed"):
        list(
            read_path.iter_twitch_messages(
                oauth_token="oauth:abc123",
                nick="rooniethecat",
                channel="ruleofrune",
                timeout_s=2,
                debug=False,
            )
        )
