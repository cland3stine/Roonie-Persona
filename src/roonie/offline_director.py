from __future__ import annotations

import re
from typing import Dict, Optional

from .offline_responders import classify_safe_info_category, library_availability_response, respond
from .types import DecisionRecord, Env, Event


_REFUSE_PATTERNS = [
    r"where do you live",
    r"address",
    r"phone number",
    r"real name",
    r"dox",
    r"ip",
]

_SENSITIVE_PATTERNS = [
    r"depressed",
    r"suicidal",
    r"self harm",
    r"kill myself",
]

_DIRECT_VERBS = (
    "fix",
    "switch",
    "change",
    "do",
    "tell",
    "show",
    "check",
    "turn",
    "mute",
    "unmute",
    "refresh",
    "restart",
    "help",
)

_UNDERSPECIFIED_REQUESTS = [r"\bfix it\b", r"\bdo that again\b"]


class OfflineDirector:
    def evaluate(self, event: Event, env: Env) -> DecisionRecord:
        message = event.message or ""
        message_stripped = message.strip()
        message_lower = message_stripped.lower()

        addressed_to_roonie = bool(event.metadata.get("is_direct_mention"))
        if "@roonie" in message_lower or message_lower.startswith("roonie"):
            addressed_to_roonie = True

        trigger_type = "banter"
        if "?" in message:
            trigger_type = "direct_question"
        elif message_lower.startswith(_DIRECT_VERBS):
            trigger_type = "direct_request"
        if addressed_to_roonie and trigger_type == "banter":
            if any(f" {v} " in f" {message_lower} " for v in _DIRECT_VERBS):
                trigger_type = "direct_request"

        ambiguity_detected = False
        if addressed_to_roonie:
            if len(message_stripped) < 4:
                ambiguity_detected = True
            if re.match(r"^\W+$", message_stripped):
                ambiguity_detected = True
            if "??" in message_stripped or "that thing" in message_lower:
                ambiguity_detected = True
            if trigger_type == "direct_request":
                if any(re.search(pat, message_lower) for pat in _UNDERSPECIFIED_REQUESTS):
                    ambiguity_detected = True
                if re.search(r"\b(it|that)\b", message_lower) and len(message_stripped.split()) <= 3:
                    ambiguity_detected = True
        else:
            if "?" in message:
                ambiguity_detected = True

        refusal_reason_code = None
        safety_classification = "allowed"
        if any(re.search(pat, message_lower) for pat in _REFUSE_PATTERNS):
            safety_classification = "refuse"
            refusal_reason_code = "REF_PRIVATE_INFO_DOXXING"
        elif any(re.search(pat, message_lower) for pat in _SENSITIVE_PATTERNS):
            safety_classification = "sensitive_no_followup"

        noop_bias_applied = True
        action = "NOOP"
        route = "none"
        routing_reason_codes = []
        selected_responder: Optional[str] = None
        utility_category: Optional[str] = None
        utility_source: Optional[str] = None
        match_confidence: Optional[str] = None

        if addressed_to_roonie and (
            trigger_type in ("direct_question", "direct_request")
            or ambiguity_detected
            or safety_classification in ("refuse", "sensitive_no_followup")
        ):
            noop_bias_applied = False

            if safety_classification == "refuse":
                action = "RESPOND_PUBLIC"
                route = "responder:refusal"
                selected_responder = route
                routing_reason_codes.append("ROUTE_REFUSAL_SAFETY")
            elif safety_classification == "sensitive_no_followup":
                action = "RESPOND_PUBLIC"
                route = "responder:neutral_ack"
                selected_responder = route
                routing_reason_codes.append("ROUTE_SENSITIVE_NO_FOLLOWUP")
            elif ambiguity_detected:
                action = "RESPOND_PUBLIC"
                route = "responder:clarify"
                selected_responder = route
                routing_reason_codes.append("ROUTE_CLARIFY_AMBIGUITY")
            elif trigger_type in ("direct_question", "direct_request"):
                action = "RESPOND_PUBLIC"
                route = "responder:policy_safe_info"
                selected_responder = route
                utility_category = classify_safe_info_category(message)
                if utility_category == "utility_library":
                    utility_source = "library_index"
                    match_confidence, _ = library_availability_response(message)
                else:
                    utility_source = "studio_profile"
                routing_reason_codes.append("ROUTE_SAFE_INFO")

        response_text = None
        if action == "RESPOND_PUBLIC":
            response_text = respond(route, event, None)

        trace = {
            "gates": {
                "addressed_to_roonie": addressed_to_roonie,
                "trigger_type": trigger_type,
                "ambiguity_detected": ambiguity_detected,
                "noop_bias_applied": noop_bias_applied,
            },
            "policy": {
                "safety_classification": safety_classification,
                "refusal_reason_code": refusal_reason_code,
            },
            "routing": {
                "selected_responder": selected_responder,
                "routing_reason_codes": routing_reason_codes,
                "utility_category": utility_category,
                "utility_source": utility_source,
                "match_confidence": match_confidence,
            },
        }

        return DecisionRecord(
            case_id=event.metadata.get("case_id", ""),
            event_id=event.event_id,
            action=action,
            route=route,
            response_text=response_text,
            trace=trace,
        )
