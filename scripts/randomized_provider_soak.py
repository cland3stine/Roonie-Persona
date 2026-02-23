from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roonie.provider_director import ProviderDirector
from roonie.types import Env, Event


ASSISTANT_SPEAK_RE = re.compile(r"\b(as an ai|how can i help you|i can help with)\b", re.IGNORECASE)
STAGE_DIR_RE = re.compile(r"\*[^*]{1,120}\*")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
EMOTE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_]{3,32}\b")
ROAST_HINT_RE = re.compile(
    r"\b(feral|laugh it up|lmao|lol|cooked|gotcha|roast|tease|calling card|spill|spilled|fine him|judging)\b",
    re.IGNORECASE,
)


@dataclass
class SoakRecord:
    timestamp_utc: str
    event_id: str
    user: str
    message: str
    action: str
    route: str
    routing_class: str
    provider_used: str
    model_used: str | None
    moderation_status: str
    suppression_reason: str | None
    provider_error_attempts: int | None
    provider_error_detail: str | None
    verdict: str
    score: int
    reasons: List[str]
    response_text: str | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "event_id": self.event_id,
            "user": self.user,
            "message": self.message,
            "action": self.action,
            "route": self.route,
            "routing_class": self.routing_class,
            "provider_used": self.provider_used,
            "model_used": self.model_used,
            "moderation_status": self.moderation_status,
            "suppression_reason": self.suppression_reason,
            "provider_error_attempts": self.provider_error_attempts,
            "provider_error_detail": self.provider_error_detail,
            "verdict": self.verdict,
            "score": self.score,
            "reasons": self.reasons,
            "response_text": self.response_text,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_secrets_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = str(raw or "").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _extract_inner_circle_handles(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    members = raw.get("members", []) if isinstance(raw, dict) else []
    out: Set[str] = set()
    if isinstance(members, list):
        for item in members:
            if not isinstance(item, dict):
                continue
            handle = str(item.get("username", "")).strip().lstrip("@").lower()
            if handle:
                out.add(handle)
    return out


def _detect_unknown_emote_tokens(response_text: str, approved_emotes: List[str]) -> List[str]:
    allowed = set(approved_emotes)
    out: List[str] = []
    for token in EMOTE_TOKEN_RE.findall(str(response_text or "")):
        if token in allowed:
            continue
        if token.startswith("@"):
            continue
        if any(ch.isupper() for ch in token[1:]) and (any(ch.isdigit() for ch in token) or token[0].islower()):
            out.append(token)
    return out


def _score_response(
    *,
    action: str,
    response_text: str | None,
    user: str,
    approved_emotes: List[str],
    inner_handles: Set[str],
) -> Tuple[str, int, List[str]]:
    if str(action).strip().upper() != "RESPOND_PUBLIC":
        return ("NOOP", 100, [f"action={action or 'UNKNOWN'}"])

    response = str(response_text or "").strip()
    score = 100
    reasons: List[str] = []

    if not response.startswith("@"):
        score -= 10
        reasons.append("missing @username at start")
    if ASSISTANT_SPEAK_RE.search(response):
        score -= 40
        reasons.append("assistant-style phrasing detected")
    if STAGE_DIR_RE.search(response):
        score -= 15
        reasons.append("stage-direction style text detected")
    if EMOJI_RE.search(response):
        score -= 20
        reasons.append("unicode emoji detected")
    if len(response) > 320:
        score -= 10
        reasons.append("response length is high for chat")

    handle = str(user or "").strip().lstrip("@").lower()
    if ROAST_HINT_RE.search(response):
        if handle in inner_handles:
            reasons.append("mild roast/banter to inner-circle (allowed)")
        else:
            score -= 30
            reasons.append("possible roast tone toward non-inner viewer")

    unknown_emote_like = _detect_unknown_emote_tokens(response, approved_emotes)
    if unknown_emote_like:
        score -= 10
        reasons.append(f"unknown emote-like token(s): {', '.join(sorted(set(unknown_emote_like))[:3])}")

    if score >= 90:
        verdict = "PASS"
    elif score >= 75:
        verdict = "CAUTION"
    else:
        verdict = "ALERT"
    return (verdict, max(0, score), reasons)


def _general_event_bank() -> List[str]:
    return [
        "@roonie what camera are you using tonight?",
        "@roonie quick check, how are the stream levels sounding?",
        "@roonie how do you stay chill during long streams?",
        "@roonie I missed chat for a minute, what is going on?",
        "@roonie can you remind me when Saturday stream starts?",
        "@roonie is Jen handling thumbnails this week?",
        "@roonie sure buddy that timing was perfect",
        "@roonie what was your favorite moment so far tonight?",
        "@roonie should I grab coffee or stick with water?",
        "@roonie where should new viewers start in this community?",
        "@roonie any tips for staying focused after work?",
        "@roonie that was funny, explain your logic there",
    ]


def _music_event_bank() -> List[str]:
    return [
        "@roonie what track is this right now?",
        "@roonie that transition was clean, what key move was that?",
        "@roonie who produced this remix?",
        "@roonie sure buddy that bassline is out of control",
        "@roonie can you explain why progressive feels emotional?",
        "@roonie is this from your rekordbox prep crate?",
        "@roonie would this fit a 2am set or nah?",
        "@roonie thanks for the set, that drop was wild",
        "@roonie what label is this on?",
        "@roonie I missed it, what was that last track ID?",
        "@roonie this build-up is ridiculous, what a tune",
        "@roonie is this release from last year or recent?",
    ]


def _event_bank(profile: str) -> List[str]:
    mode = str(profile or "").strip().lower()
    if mode == "music":
        return _music_event_bank()
    if mode == "mixed":
        return _general_event_bank() + _music_event_bank()
    return _general_event_bank()


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a controlled randomized-provider soak with style scoring.")
    parser.add_argument("--events", type=int, default=24, help="Number of events to run.")
    parser.add_argument(
        "--profile",
        choices=["general", "mixed", "music"],
        default="general",
        help="Prompt profile: general exercises random_approved across all providers; music follows music-route policy.",
    )
    parser.add_argument("--seed", type=int, default=220226, help="Deterministic seed for prompt/user rotation.")
    parser.add_argument("--pause-seconds", type=float, default=0.25, help="Pause between events.")
    parser.add_argument("--session-size", type=int, default=4, help="Events per synthetic session.")
    parser.add_argument("--show-samples", type=int, default=2, help="Sample responses to print per provider.")
    parser.add_argument("--output", default="", help="Optional explicit output JSONL path.")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    _load_secrets_env(repo_root / "config" / "secrets.env")
    os.environ["ROONIE_ENABLE_LIVE_PROVIDER_NETWORK"] = "1"

    missing = []
    if not str(os.getenv("OPENAI_API_KEY", "")).strip():
        missing.append("OPENAI_API_KEY")
    if not str(os.getenv("GROK_API_KEY", "")).strip() and not str(os.getenv("XAI_API_KEY", "")).strip():
        missing.append("GROK_API_KEY")
    if not str(os.getenv("ANTHROPIC_API_KEY", "")).strip():
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print("ERROR: missing required keys:", ", ".join(missing))
        return 2

    out_path = Path(args.output).resolve() if str(args.output).strip() else (
        repo_root
        / "logs"
        / "soak_runs"
        / f"{datetime.now().strftime('%Y-%m-%d')}_randomized_provider_soak_{datetime.now().strftime('%H%M%S')}.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    inner_handles = _extract_inner_circle_handles(repo_root / "data" / "inner_circle.json")
    users = [
        "cland3stine",
        "nightdrive77",
        "fraggyxx",
        "vinylrune",
        "c0rcyra",
        "latehourlistener",
        "ruleofrune",
        "basslinepilot",
    ]
    prompts = _event_bank(str(args.profile))
    rng = random.Random(int(args.seed))
    director = ProviderDirector()
    env = Env(offline=False)

    records: List[SoakRecord] = []
    provider_samples: Dict[str, List[str]] = defaultdict(list)

    print("Randomized provider soak starting")
    print(f"events={int(args.events)} profile={str(args.profile)} seed={int(args.seed)} output={out_path}")

    for idx in range(int(args.events)):
        prompt = prompts[rng.randrange(len(prompts))]
        user = users[rng.randrange(len(users))]
        event_id = f"SOAK-{idx + 1:04d}"
        session_id = f"soak-{(idx // max(1, int(args.session_size))) + 1:03d}"
        event = Event(
            event_id=event_id,
            message=prompt,
            actor="viewer",
            metadata={
                "user": user,
                "is_direct_mention": True,
                "mode": "live",
                "case_id": "soak_randomized_provider",
                "session_id": session_id,
            },
        )

        decision = director.evaluate(event, env)
        trace = decision.trace if isinstance(decision.trace, dict) else {}
        proposal = trace.get("proposal", {}) if isinstance(trace.get("proposal", {}), dict) else {}
        routing = trace.get("routing", {}) if isinstance(trace.get("routing", {}), dict) else {}
        behavior = trace.get("behavior", {}) if isinstance(trace.get("behavior", {}), dict) else {}

        provider_used = str(
            proposal.get("provider_used")
            or routing.get("provider_selected")
            or "none"
        ).strip().lower() or "none"
        model_used_raw = proposal.get("model_used") or routing.get("model_selected")
        model_used = str(model_used_raw).strip() if model_used_raw is not None else None
        moderation_status = str(
            proposal.get("moderation_status")
            or routing.get("moderation_result")
            or "not_applicable"
        ).strip().lower() or "not_applicable"
        routing_class = str(routing.get("routing_class") or "unknown").strip().lower() or "unknown"
        suppression_reason_raw = trace.get("suppression_reason")
        suppression_reason = str(suppression_reason_raw).strip() if suppression_reason_raw is not None else None

        attempts_raw = routing.get("provider_error_attempts")
        attempts: int | None
        try:
            attempts = int(attempts_raw) if attempts_raw is not None else None
        except (TypeError, ValueError):
            attempts = None
        error_detail_raw = trace.get("provider_error_detail")
        error_detail = str(error_detail_raw).strip() if error_detail_raw is not None else None

        approved_emotes_raw = behavior.get("approved_emotes", [])
        approved_emotes = [str(item).strip() for item in approved_emotes_raw if str(item).strip()] if isinstance(approved_emotes_raw, list) else []
        verdict, score, reasons = _score_response(
            action=str(decision.action),
            response_text=decision.response_text,
            user=user,
            approved_emotes=approved_emotes,
            inner_handles=inner_handles,
        )

        record = SoakRecord(
            timestamp_utc=_utc_now(),
            event_id=event_id,
            user=user,
            message=prompt,
            action=str(decision.action),
            route=str(decision.route),
            routing_class=routing_class,
            provider_used=provider_used,
            model_used=model_used,
            moderation_status=moderation_status,
            suppression_reason=suppression_reason,
            provider_error_attempts=attempts,
            provider_error_detail=error_detail,
            verdict=verdict,
            score=score,
            reasons=reasons,
            response_text=decision.response_text,
        )
        records.append(record)

        preview = str(decision.response_text or "").replace("\n", " ").strip()
        if len(preview) > 140:
            preview = preview[:137].rstrip() + "..."
        provider_samples[provider_used].append(preview)
        print(
            f"{event_id} provider={provider_used} action={decision.action} "
            f"moderation={moderation_status} verdict={verdict} score={score}"
        )
        if suppression_reason:
            print(f"  suppression_reason={suppression_reason}")
        if attempts:
            print(f"  provider_error_attempts={attempts} detail={error_detail or 'unknown'}")

        if float(args.pause_seconds) > 0:
            time.sleep(float(args.pause_seconds))

    with out_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    provider_counts = Counter(rec.provider_used for rec in records)
    routing_class_counts = Counter(rec.routing_class for rec in records)
    verdict_counts = Counter(rec.verdict for rec in records)
    moderation_counts = Counter(rec.moderation_status for rec in records)
    suppression_counts = Counter(str(rec.suppression_reason or "none") for rec in records)

    scored = [rec.score for rec in records]
    provider_avg: Dict[str, float] = {}
    for provider in sorted(provider_counts):
        scores = [rec.score for rec in records if rec.provider_used == provider]
        provider_avg[provider] = round(float(statistics.mean(scores)), 2) if scores else 0.0

    provider_errors = [rec for rec in records if rec.provider_error_attempts is not None]
    caution_or_alert = [rec for rec in records if rec.verdict in {"CAUTION", "ALERT"}]

    print("\nSoak summary")
    print(f"records={len(records)} avg_score={round(float(statistics.mean(scored)), 2) if scored else 0.0}")
    print(f"providers={dict(provider_counts)}")
    print(f"routing_classes={dict(routing_class_counts)}")
    print(f"provider_avg_score={provider_avg}")
    print(f"verdicts={dict(verdict_counts)}")
    print(f"moderation={dict(moderation_counts)}")
    print(f"suppressions={dict(suppression_counts)}")
    print(f"provider_errors={len(provider_errors)}")
    print(f"style_flags={len(caution_or_alert)}")
    print(f"details_file={out_path}")

    sample_cap = max(0, int(args.show_samples))
    if sample_cap > 0:
        print("\nSample responses by provider")
        for provider in sorted(provider_samples):
            shown = [text for text in provider_samples[provider] if text][:sample_cap]
            if not shown:
                continue
            print(f"[{provider}]")
            for line in shown:
                print(f"- {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
