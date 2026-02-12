"""Twitch output adapter (live send via IRC)."""

from __future__ import annotations

import os
import socket
import ssl
from typing import Any, Dict

HOST = "irc.chat.twitch.tv"
PORT = 6697


class TwitchOutputAdapter:
    def handle_output(self, envelope: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if envelope.get("type") != "RESPOND_PUBLIC":
            return
        if ctx.get("mode") not in {"live", "replay"}:
            return

        if ctx.get("mode") == "replay":
            print("[TwitchOutputAdapter] replay mode: output suppressed")
            return

        if os.getenv("TWITCH_OUTPUT_ENABLED") != "1":
            return
        if os.getenv("ROONIE_OUTPUT_DISABLED") == "1":
            return

        channel = os.getenv("TWITCH_CHANNEL", "").lstrip("#")
        if not channel:
            print("[TwitchOutputAdapter] live send skipped: no TWITCH_CHANNEL")
            return
        nick = os.getenv("TWITCH_BOT_NICK")
        token = os.getenv("TWITCH_OAUTH_TOKEN")
        if not nick or not token:
            print("[TwitchOutputAdapter] live send skipped: missing auth")
            return

        text = envelope.get("response_text") or ""
        if not text:
            return

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((HOST, PORT))
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=HOST)
            sock.settimeout(5.0)
            sock.sendall(f"PASS {token}\r\n".encode("utf-8"))
            sock.sendall(f"NICK {nick}\r\n".encode("utf-8"))
            sock.sendall(f"JOIN #{channel}\r\n".encode("utf-8"))
            sock.sendall(f"PRIVMSG #{channel} :{text}\r\n".encode("utf-8"))
            sock.sendall(b"QUIT\r\n")
            sock.close()
            print(f"[TwitchOutputAdapter] send ok -> #{channel}")
        except Exception as exc:
            print(f"[TwitchOutputAdapter] send failed: {exc}")
        return
