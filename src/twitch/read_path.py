from __future__ import annotations

import re
import socket
import ssl
from dataclasses import dataclass
from typing import Iterator

# Twitch may prepend IRCv3 tags to PRIVMSG lines.
PRIVMSG_RE = re.compile(
    r"^(?:@(?P<tags>[^\s]+)\s+)?:(?P<nick>[^!]+)![^ ]+ PRIVMSG #(?P<chan>[^ ]+) :(?P<msg>.*)$"
)
NOTICE_RE = re.compile(
    r"^(?:@(?P<tags>[^\s]+)\s+)?:(?P<server>[^ ]+)\s+NOTICE\s+(?P<target>[^ ]+)\s+:(?P<msg>.*)$"
)
RECONNECT_RE = re.compile(r"^(?:@(?P<tags>[^\s]+)\s+)?:(?P<server>[^ ]+)\s+RECONNECT\b")


@dataclass(frozen=True)
class TwitchMsg:
    nick: str
    channel: str
    message: str
    raw: str


def _connect_tls(host: str, port: int, timeout_s: int) -> socket.socket:
    raw = socket.create_connection((host, port), timeout=timeout_s)
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(raw, server_hostname=host)
    # Use blocking mode for the IRC read stream; timeouts on makefile-backed
    # sockets can poison subsequent reads ("cannot read from timed out object").
    tls.settimeout(None)
    return tls


def iter_twitch_messages(
    *,
    oauth_token: str,
    nick: str,
    channel: str,
    timeout_s: int = 20,
    debug: bool = False,
    host: str = "irc.chat.twitch.tv",
    port: int = 6697,
) -> Iterator[TwitchMsg]:
    """
    Read-only Twitch IRC iterator.
    - Handles PING/PONG
    - Handles auth NOTICE failures explicitly
    - Yields PRIVMSG as TwitchMsg
    """
    if not oauth_token.startswith("oauth:"):
        raise ValueError("TWITCH_OAUTH_TOKEN must start with 'oauth:'")
    chan = channel.lstrip("#").lower()

    s = _connect_tls(host, port, timeout_s)
    f = s.makefile("rwb", buffering=0)

    def send(line: str) -> None:
        f.write((line + "\r\n").encode("utf-8"))

    send("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
    send(f"PASS {oauth_token}")
    send(f"NICK {nick}")
    send(f"JOIN #{chan}")

    while True:
        try:
            raw = f.readline()
        except (TimeoutError, socket.timeout):
            # Keep the connection alive across idle periods.
            continue
        except OSError as exc:
            # Force reconnect on stream/socket failures.
            raise RuntimeError(f"Twitch IRC read failed: {exc}") from exc
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")

        if debug:
            # Print server/system lines to help diagnose auth/join issues
            if (" NOTICE " in line) or (" 001 " in line) or (" 002 " in line):
                print("[IRC]", line)

        if line.startswith("PING "):
            # Reply with exact payload
            payload = line.split(" ", 1)[1]
            send(f"PONG {payload}")
            continue

        notice = NOTICE_RE.match(line)
        if notice:
            msg = str(notice.group("msg") or "").strip()
            lowered = msg.lower()
            if (
                "login authentication failed" in lowered
                or "improperly formatted auth" in lowered
                or "login unsuccessful" in lowered
                or "authentication failed" in lowered
            ):
                raise RuntimeError(f"Twitch IRC auth failed: {msg or 'NOTICE'}")
            if debug:
                print("[IRC]", line)
            continue

        # Twitch can request the client reconnect.
        if RECONNECT_RE.match(line):
            raise RuntimeError("Twitch IRC requested reconnect")

        m = PRIVMSG_RE.match(line)
        if m:
            yield TwitchMsg(
                nick=m.group("nick"),
                channel=m.group("chan"),
                message=m.group("msg"),
                raw=line,
            )
