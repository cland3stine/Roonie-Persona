from __future__ import annotations

import re
import socket
import ssl
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

PRIVMSG_RE = re.compile(r"^:(?P<nick>[^!]+)![^ ]+ PRIVMSG #(?P<chan>[^ ]+) :(?P<msg>.*)$")

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
    - Yields PRIVMSG as TwitchMsg
    """
    if not oauth_token.startswith("oauth:"):
        raise ValueError("TWITCH_OAUTH_TOKEN must start with 'oauth:'")
    chan = channel.lstrip("#").lower()

    s = _connect_tls(host, port, timeout_s)
    f = s.makefile("rwb", buffering=0)

    def send(line: str) -> None:
        f.write((line + "\r\n").encode("utf-8"))

    send(f"PASS {oauth_token}")
    send(f"NICK {nick}")
    send(f"JOIN #{chan}")

    while True:
        try:
            raw = f.readline()
        except (TimeoutError, socket.timeout, OSError):
            # Keep the connection alive across idle periods.
            continue
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")


        if debug:
            # Print server/system lines to help diagnose auth/join issues
            if (" NOTICE " in line) or line.split(" ", 1)[0].isdigit() or " 001 " in line or " 002 " in line:
                print("[IRC]", line)

        if line.startswith("PING "):
            # Reply with exact payload
            payload = line.split(" ", 1)[1]
            send(f"PONG {payload}")
            continue

        m = PRIVMSG_RE.match(line)
        if m:
            yield TwitchMsg(
                nick=m.group("nick"),
                channel=m.group("chan"),
                message=m.group("msg"),
                raw=line,
            )

