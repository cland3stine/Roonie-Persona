# ROONIE Phase 0 multi-model test brain
# OpenAI = Director
# Claude Sonnet 4.5 + Grok xAI as responders

import os
import re
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any, Literal

from dotenv import load_dotenv
from openai import OpenAI
import requests

# Claude
try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None

# Local fallback
try:
    import ollama
except Exception:
    ollama = None

load_dotenv()

Action = Literal["NOOP", "CHAT_REPLY"]
ModelName = Literal["openai", "claude", "grok"]


@dataclass
class DirectorDecision:
    action: Action
    model: ModelName
    reason: str


class RoonieBrain:
    def __init__(self):
        # ===== OpenAI =====
        self.openai_client = OpenAI()
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-5.2")
        self.director_model = os.getenv("ROONIE_DIRECTOR_MODEL", self.openai_model)

        # ===== Claude =====
        self.anthropic_client = None
        self.claude_model = os.getenv("CLAUDE_MODEL")
        if os.getenv("ANTHROPIC_API_KEY") and Anthropic:
            self.anthropic_client = Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY")
            )

        # ===== Grok =====
        self.grok_api_key = os.getenv("GROK_API_KEY")
        self.grok_model = os.getenv("GROK_MODEL")

        # ===== Tuning =====
        self.temperature = float(os.getenv("ROONIE_TEMPERATURE", "0.55"))
        self.max_tokens = int(os.getenv("ROONIE_MAX_OUTPUT_TOKENS", "140"))
        self.local_model = os.getenv("ROONIE_LOCAL_MODEL", "qwen3:32b")

        # ===== System Prompt (ROONIE Canon) =====
        self.system_prompt = """You are Roonie: a text-only Twitch chat regular for Art and Corcyra's progressive house streams.

Default behavior: say less. If you have nothing valuable to add, output an empty string.

Hard rules:
- Commentary only. Never moderate or instruct mods.
- Never reveal personal, private, or location-specific info.
- No parasocial behavior, no roleplay, no “as an AI”.
- No criticism of tracks on stream.
- Avoid drama. De-escalate briefly or stay silent.

Style:
- 1–2 lines max, ~10–25 words.
- No stage directions (*actions*).
- Emotes: 0–1 max, only if natural.
"""

        # ===== Director Prompt =====
        self.director_prompt = """You are the Director for Roonie.

Decide whether Roonie should respond at all, and which model should respond.

Rules:
- Prefer NOOP unless response adds clear value.
- Silence is success.
- Keep Roonie minimal and safe.

Return STRICT JSON ONLY:
{"action":"NOOP|CHAT_REPLY","model":"openai|claude|grok","reason":"short"}
"""

    # ======================
    # Public entry point
    # ======================
    def chat(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        decision = self._director_decide(message, context)

        if decision.action == "NOOP":
            return ""

        if decision.model == "claude":
            return self._post_filter(self._claude_chat(message, context))
        if decision.model == "grok":
            return self._post_filter(self._grok_chat(message, context))

        return self._post_filter(self._openai_chat(message, context))

    # ======================
    # Director
    # ======================
    def _director_decide(self, message: str, context: Optional[Dict[str, Any]]) -> DirectorDecision:
        payload = {
            "viewer_message": message,
            "context": context or {},
        }

        try:
            r = self.openai_client.responses.create(
                model=self.director_model,
                input=[
                    {"role": "system", "content": self.director_prompt},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                temperature=0.2,
                max_output_tokens=120,
            )
            data = json.loads(r.output_text.strip())
            return DirectorDecision(
                action=data.get("action", "NOOP"),
                model=data.get("model", "openai"),
                reason=data.get("reason", "")
            )
        except Exception as e:
            print("[DIRECTOR ERROR]", e)
            return DirectorDecision("CHAT_REPLY", "openai", "director_fallback")

    # ======================
    # OpenAI responder
    # ======================
    def _openai_chat(self, message, context):
        prompt = self._build_prompt(message, context)
        r = self.openai_client.responses.create(
            model=self.openai_model,
            input=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        print("[DEBUG] OpenAI responder")
        return r.output_text.strip()

    # ======================
    # Claude responder
    # ======================
    def _claude_chat(self, message, context):
        if not self.anthropic_client:
            return self._openai_chat(message, context)

        prompt = self._build_prompt(message, context)
        print("[DEBUG] Claude responder")

        msg = self.anthropic_client.messages.create(
            model=self.claude_model,
            system=self.system_prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )

        return "".join(
            block.text for block in msg.content if block.type == "text"
        )

    # ======================
    # Grok responder (xAI)
    # ======================
    def _grok_chat(self, message, context):
        if not self.grok_api_key:
            return self._openai_chat(message, context)

        print("[DEBUG] Grok responder")
        prompt = self._build_prompt(message, context)

        headers = {
            "Authorization": f"Bearer {self.grok_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.grok_model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=20,
        )
        r.raise_for_status()

        return r.json()["choices"][0]["message"]["content"]

    # ======================
    # Helpers
    # ======================
    def _build_prompt(self, message, context):
        parts = []
        if context:
            if context.get("current_track"):
                parts.append(f"Now playing: {context['current_track']}")
        parts.append(f"Viewer: {message}")
        parts.append("Respond as Roonie, or stay silent.")
        return "\n".join(parts)

    def _post_filter(self, text: str) -> str:
        if not text:
            return ""

        t = text.strip()
        t = re.sub(r"\*[^*]{1,60}\*", "", t)
        t = re.sub(r"as an ai|language model", "", t, flags=re.I)
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        t = "\n".join(lines[:2])
        words = t.split()
        if len(words) > 45:
            t = " ".join(words[:45]) + "…"
        return t.strip()


# ======================
# Manual test
# ======================
if __name__ == "__main__":
    brain = RoonieBrain()
    while True:
        msg = input("Chat> ")
        if not msg:
            break
        print("Roonie>", brain.chat(msg))
