"""Twitch output adapter (live send via IRC)."""

from __future__ import annotations

import logging
import os
import socket
import ssl
from typing import Any, Dict

HOST = "irc.chat.twitch.tv"
PORT = 6697
logger = logging.getLogger(__name__)


class TwitchOutputAdapter:
    @staticmethod
    def _send_error_code(exc: Exception) -> str:
        if isinstance(exc, TimeoutError):
            return "TIMEOUT"
        if isinstance(exc, ssl.SSLError):
            return "TLS_ERROR"
        if isinstance(exc, OSError):
            return "NETWORK_ERROR"
        return "UNKNOWN_ERROR"

    def handle_output(self, envelope: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        channel = os.getenv("TWITCH_CHANNEL", "").lstrip("#")
        if not channel:
            return {"sent": False, "reason": "NO_CHANNEL"}
        nick = os.getenv("TWITCH_BOT_NICK")
        if not nick:
            return {"sent": False, "reason": "NO_BOT_NICK"}
        token = os.getenv("TWITCH_OAUTH_TOKEN")
        if not token:
            return {"sent": False, "reason": "NO_OAUTH_TOKEN"}

        text = envelope.get("response_text") or ""
        if not text:
            return {"sent": False, "reason": "EMPTY_TEXT"}

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
            return {"sent": True, "reason": "OK"}
        except Exception as exc:
            error_code = self._send_error_code(exc)
            logger.warning("Twitch send failed (%s)", error_code)
            return {
                "sent": False,
                "reason": f"SEND_FAILED_{error_code}",
                "error_code": error_code,
            }
