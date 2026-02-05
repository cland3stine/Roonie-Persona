"""Twitch IRC read-only adapter.

Dry run / env:
- TWITCH_CHANNEL (default: cland3stine)
- Optional auth: TWITCH_BOT_NICK, TWITCH_OAUTH_TOKEN (oauth:...)
"""

from __future__ import annotations

import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_shim import record_run

HOST = "irc.chat.twitch.tv"
PORT = 6667


def _now_session_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"twitch-{ts}"


def _parse_tags(tag_str: str) -> Dict[str, str]:
    tags: Dict[str, str] = {}
    for part in tag_str.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k] = v
    return tags


def _parse_privmsg(line: str) -> Optional[Tuple[str, str]]:
    # Example:
    # @badge-info=;badges=;display-name=User;... :user!user@user.tmi.twitch.tv PRIVMSG #chan :message
    if " PRIVMSG " not in line:
        return None

    tags = {}
    rest = line
    if rest.startswith("@"):
        tag_part, rest = rest.split(" ", 1)
        tags = _parse_tags(tag_part[1:])

    if " PRIVMSG " not in rest or " :" not in rest:
        return None

    prefix, msg = rest.split(" :", 1)
    user = None
    display = tags.get("display-name")
    if display:
        user = display
    else:
        # prefix starts with :login!login@...
        if prefix.startswith(":") and "!" in prefix:
            user = prefix[1:].split("!", 1)[0]

    if not user:
        return None

    return user, msg


def main() -> int:
    nick = os.getenv("TWITCH_BOT_NICK")
    token = os.getenv("TWITCH_OAUTH_TOKEN")
    channel = os.getenv("TWITCH_CHANNEL", "cland3stine").lstrip("#")
    fixture_hint = os.getenv("TWITCH_FIXTURE_HINT")
    join_channel = f"#{channel}"

    if not nick:
        nick = "justinfan12345"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect((HOST, PORT))
    except Exception as exc:
        print(f"fatal: connect failed: {exc}", file=sys.stderr, flush=True)
        return 1
    sock.settimeout(0.5)

    # Auth optional
    if token and os.getenv("TWITCH_BOT_NICK"):
        sock.sendall(f"PASS {token}\r\n".encode("utf-8"))

    sock.sendall(b"CAP REQ :twitch.tv/tags\r\n")
    sock.sendall(b"CAP REQ :twitch.tv/commands\r\n")
    sock.sendall(f"NICK {nick}\r\n".encode("utf-8"))
    sock.sendall(f"JOIN {join_channel}\r\n".encode("utf-8"))

    print(f"connected/joined {join_channel}", flush=True)

    buffer = ""
    events: List[Dict[str, object]] = []
    event_id = 1
    last_flush = time.time()

    def flush() -> None:
        nonlocal events, last_flush
        if not events:
            last_flush = time.time()
            return
        payload = {
            "session_id": _now_session_id(),
            "inputs": events,
        }
        if fixture_hint:
            payload["fixture_hint"] = fixture_hint
        record_run.run_payload(payload, emit_outputs=False)
        events = []
        last_flush = time.time()

    try:
        while True:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
            except socket.timeout:
                data = b""

            while "\r\n" in buffer:
                line, buffer = buffer.split("\r\n", 1)
                if line.startswith("PING"):
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue

                parsed = _parse_privmsg(line)
                if parsed:
                    user, msg = parsed
                    is_direct = "@roonie" in msg.lower()
                    events.append(
                        {
                            "event_id": f"evt-{event_id}",
                            "message": msg,
                            "metadata": {
                                "user": user,
                                "platform": "twitch",
                                "is_direct_mention": is_direct,
                            },
                        }
                    )
                    event_id += 1

                    if len(events) >= 3:
                        flush()

            if (time.time() - last_flush) >= 10:
                flush()

    except KeyboardInterrupt:
        pass
    finally:
        try:
            flush()
        finally:
            sock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
