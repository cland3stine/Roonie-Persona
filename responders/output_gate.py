from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List

from roonie.behavior_spec import cooldown_for_category

_LAST_EMIT_TS = 0.0
_LAST_EMIT_BY_KEY: Dict[str, float] = {}
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_]{3,32}\b")


def _emit_cooldown_seconds() -> float:
    raw = os.getenv("ROONIE_OUTPUT_RATE_LIMIT_SECONDS", "6")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 6.0


def _decision_trace(decision: Dict[str, Any]) -> Dict[str, Any]:
    trace = decision.get("trace", {}) if isinstance(decision, dict) else {}
    return trace if isinstance(trace, dict) else {}


def _decision_session_id(decision: Dict[str, Any]) -> str | None:
    proposal = _decision_trace(decision).get("proposal", {})
    if not isinstance(proposal, dict):
        return None
    sid = str(proposal.get("session_id", "")).strip()
    return sid or None


def _decision_category(decision: Dict[str, Any]) -> str:
    behavior = _decision_trace(decision).get("behavior", {})
    if not isinstance(behavior, dict):
        return "OTHER"
    category = str(behavior.get("category", "OTHER")).strip().upper()
    return category or "OTHER"


def _decision_approved_emotes(decision: Dict[str, Any]) -> List[str]:
    behavior = _decision_trace(decision).get("behavior", {})
    if not isinstance(behavior, dict):
        return []
    raw = behavior.get("approved_emotes", [])
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw[:24]:
        if isinstance(item, dict):
            if item.get("denied", False):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                out.append(name)
        else:
            text = str(item or "").strip()
            if text:
                out.append(text)
    return out


def _looks_like_emote(token: str) -> bool:
    text = str(token or "").strip()
    if not text:
        return False
    if "_" in text:
        return True
    for idx in range(1, len(text)):
        if text[idx].isupper() and text[idx - 1].islower():
            return True
    return False


def _disallowed_emote_in_text(text: str, allowed: List[str]) -> str | None:
    allowed_set = {item.strip() for item in allowed if item.strip()}
    if not allowed_set:
        return None
    for token in _TOKEN_RE.findall(str(text or "")):
        if _looks_like_emote(token) and token not in allowed_set:
            return token
    return None


def maybe_emit(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    global _LAST_EMIT_TS, _LAST_EMIT_BY_KEY

    outputs: List[Dict[str, Any]] = []

    dry_run = str(os.getenv("ROONIE_DRY_RUN") or os.getenv("ROONIE_READ_ONLY_MODE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if os.getenv("ROONIE_OUTPUT_DISABLED") == "1":
        for d in decisions:
            outputs.append(
                {
                    "event_id": d.get("event_id"),
                    "session_id": _decision_session_id(d),
                    "category": _decision_category(d),
                    "emitted": False,
                    "reason": "OUTPUT_DISABLED",
                    "sink": "stdout",
                }
            )
        return outputs

    now = time.time()
    allow_emit = (now - _LAST_EMIT_TS) >= _emit_cooldown_seconds()

    for d in decisions:
        trace = _decision_trace(d)
        category = _decision_category(d)
        explicit_suppression = str(
            trace.get("suppression_reason") or trace.get("provider_block_reason") or ""
        ).strip()

        if d.get("action") == "RESPOND_PUBLIC":
            if dry_run:
                # Canon: DRY_RUN suppresses outbound posting attempts (no adapter calls).
                # Keep primary suppression reason stable and surface detail via trace.
                try:
                    trace = d.setdefault("trace", {}) if isinstance(d, dict) else {}
                    if isinstance(trace, dict):
                        policy = trace.setdefault("policy", {})
                        if isinstance(policy, dict) and not policy.get("refusal_reason_code"):
                            policy["refusal_reason_code"] = "DRY_RUN"
                        if not trace.get("suppression_reason"):
                            trace["suppression_reason"] = "DRY_RUN"
                except Exception:
                    pass
                outputs.append(
                    {
                        "event_id": d.get("event_id"),
                        "session_id": _decision_session_id(d),
                        "category": category,
                        "emitted": False,
                        "reason": "DRY_RUN",
                        "sink": "stdout",
                    }
                )
                continue

            approved_emotes = _decision_approved_emotes(d)
            disallowed_emote = _disallowed_emote_in_text(str(d.get("response_text") or ""), approved_emotes)
            if disallowed_emote:
                outputs.append(
                    {
                        "event_id": d.get("event_id"),
                        "session_id": _decision_session_id(d),
                        "category": category,
                        "emitted": False,
                        "reason": "DISALLOWED_EMOTE",
                        "sink": "stdout",
                    }
                )
                continue

            cooldown_key, cooldown_window, cooldown_reason = cooldown_for_category(category)
            if cooldown_key and cooldown_window > 0.0 and cooldown_reason:
                last_ts = float(_LAST_EMIT_BY_KEY.get(cooldown_key, 0.0))
                elapsed = now - last_ts
                if elapsed < cooldown_window:
                    remaining = max(0.0, cooldown_window - elapsed)
                    outputs.append(
                        {
                            "event_id": d.get("event_id"),
                            "session_id": _decision_session_id(d),
                            "category": category,
                            "emitted": False,
                            "reason": cooldown_reason,
                            "cooldown_key": cooldown_key,
                            "cooldown_remaining_seconds": round(remaining, 3),
                            "sink": "stdout",
                        }
                    )
                    continue

        action = str(d.get("action") or "").strip().upper()
        if action != "RESPOND_PUBLIC":
            default_reason = "NOOP" if action == "NOOP" else "ACTION_NOT_ALLOWED"
            outputs.append(
                {
                    "event_id": d.get("event_id"),
                    "session_id": _decision_session_id(d),
                    "category": category,
                    "emitted": False,
                    "reason": explicit_suppression or default_reason,
                    "sink": "stdout",
                }
            )
            continue

        if not allow_emit:
            outputs.append(
                {
                    "event_id": d.get("event_id"),
                    "session_id": _decision_session_id(d),
                    "category": category,
                    "emitted": False,
                    "reason": "RATE_LIMIT",
                    "sink": "stdout",
                }
            )
            continue

        outputs.append(
            {
                "event_id": d.get("event_id"),
                "session_id": _decision_session_id(d),
                "category": category,
                "emitted": True,
                "reason": "EMITTED",
                "sink": "stdout",
            }
        )
        cooldown_key, cooldown_window, cooldown_reason = cooldown_for_category(category)
        if cooldown_key and cooldown_window > 0.0 and cooldown_reason:
            _LAST_EMIT_BY_KEY[cooldown_key] = now
        _LAST_EMIT_TS = now
        allow_emit = False

    return outputs
