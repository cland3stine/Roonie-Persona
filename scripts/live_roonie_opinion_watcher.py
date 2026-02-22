from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


EMIT_RE = re.compile(
    r"\[LiveChatBridge\]\s+emitted(?:\(retry\))?\s+event_id=(?P<event_id>\S+)\s+user=(?P<user>\S+)\s+reason=(?P<reason>\S+)"
)
NO_EMIT_RE = re.compile(
    r"\[LiveChatBridge\]\s+processed\(no-emit\)\s+event_id=(?P<event_id>\S+)\s+reason=(?P<reason>\S+)"
)
OUTPUT_DISABLED_REASONS = {"OUTPUT_DISABLED", "DRY_RUN"}

ASSISTANT_SPEAK_RE = re.compile(r"\b(as an ai|how can i help you|i can help with)\b", re.IGNORECASE)
STAGE_DIR_RE = re.compile(r"\*[^*]{1,120}\*")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
EMOTE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_]{3,32}\b")

# Mild roast detectors. Purposefully conservative.
ROAST_HINT_RE = re.compile(
    r"\b(feral|laugh it up|lmao|lol|cooked|gotcha|roast|tease|calling card|spill|spilled|fine him|judging)\b",
    re.IGNORECASE,
)


@dataclass
class EventSignal:
    event_id: str
    user: str
    reason: str
    emitted: bool
    raw_line: str


@dataclass
class EventPayload:
    event_id: str
    user: str
    message: str
    action: str
    response_text: str
    output_reason: str
    started_at: str
    trace: Dict[str, Any]
    approved_emotes: List[str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_handle(text: str) -> str:
    return str(text or "").strip().lstrip("@").lower()


def _normalize_emote_name(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.match(r"^([A-Za-z][A-Za-z0-9_]{2,31})\b", text)
    if not match:
        return ""
    return str(match.group(1)).strip()


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
            handle = _normalize_handle(item.get("username", ""))
            if handle:
                out.add(handle)
    return out


def _iter_new_lines(path: Path, *, poll_seconds: float, start_at_end: bool) -> Iterable[str]:
    position = 0
    initialized = False
    while True:
        if not path.exists():
            time.sleep(poll_seconds)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(poll_seconds)
            continue
        if not initialized:
            position = size if start_at_end else 0
            initialized = True
        if size < position:
            # Log rotated/truncated.
            position = 0
        if size == position:
            time.sleep(poll_seconds)
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(position)
                chunk = fh.read()
                position = fh.tell()
        except OSError:
            time.sleep(poll_seconds)
            continue
        for line in chunk.splitlines():
            text = line.strip()
            if text:
                yield text


def _read_last_lines(path: Path, *, count: int) -> List[str]:
    if count <= 0 or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines[-count:] if line.strip()]


def _parse_signal(line: str) -> Optional[EventSignal]:
    m_emit = EMIT_RE.search(line)
    if m_emit:
        return EventSignal(
            event_id=str(m_emit.group("event_id") or "").strip(),
            user=str(m_emit.group("user") or "").strip(),
            reason=str(m_emit.group("reason") or "").strip() or "UNKNOWN",
            emitted=True,
            raw_line=line,
        )
    m_no_emit = NO_EMIT_RE.search(line)
    if m_no_emit:
        return EventSignal(
            event_id=str(m_no_emit.group("event_id") or "").strip(),
            user="",
            reason=str(m_no_emit.group("reason") or "").strip() or "UNKNOWN",
            emitted=False,
            raw_line=line,
        )
    return None


def _find_run_file_for_event(runs_dir: Path, event_id: str, *, wait_seconds: float = 2.0) -> Optional[Path]:
    if not event_id:
        return None
    deadline = time.time() + max(0.0, float(wait_seconds))
    while True:
        matches = list(runs_dir.glob(f"*{event_id}.json"))
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]
        if time.time() >= deadline:
            return None
        time.sleep(0.1)


def _load_event_payload(run_path: Path, event_id: str) -> Optional[EventPayload]:
    try:
        raw = json.loads(run_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    inputs = raw.get("inputs", []) if isinstance(raw, dict) else []
    decisions = raw.get("decisions", []) if isinstance(raw, dict) else []
    outputs = raw.get("outputs", []) if isinstance(raw, dict) else []
    started_at = str(raw.get("started_at", "")).strip()

    event_input: Dict[str, Any] = {}
    for item in inputs if isinstance(inputs, list) else []:
        if isinstance(item, dict) and str(item.get("event_id", "")).strip() == event_id:
            event_input = item
            break
    event_decision: Dict[str, Any] = {}
    for item in decisions if isinstance(decisions, list) else []:
        if isinstance(item, dict) and str(item.get("event_id", "")).strip() == event_id:
            event_decision = item
            break
    event_output: Dict[str, Any] = {}
    for item in outputs if isinstance(outputs, list) else []:
        if isinstance(item, dict) and str(item.get("event_id", "")).strip() == event_id:
            event_output = item
            break

    if not event_input and not event_decision:
        return None

    metadata = event_input.get("metadata", {}) if isinstance(event_input, dict) else {}
    user = str(metadata.get("user", "")).strip()
    message = str(event_input.get("message", "")).strip()
    action = str(event_decision.get("action", "")).strip().upper()
    response_text = str(event_decision.get("response_text", "")).strip()
    output_reason = str(event_output.get("reason", "")).strip() or "UNKNOWN"
    trace = event_decision.get("trace", {}) if isinstance(event_decision, dict) else {}
    if not isinstance(trace, dict):
        trace = {}
    behavior = trace.get("behavior", {})
    approved_emotes_raw = behavior.get("approved_emotes", []) if isinstance(behavior, dict) else []
    approved_emotes: List[str] = []
    if isinstance(approved_emotes_raw, list):
        for item in approved_emotes_raw:
            name = _normalize_emote_name(item)
            if name:
                approved_emotes.append(name)

    return EventPayload(
        event_id=event_id,
        user=user,
        message=message,
        action=action,
        response_text=response_text,
        output_reason=output_reason,
        started_at=started_at,
        trace=trace,
        approved_emotes=approved_emotes,
    )


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


def _build_opinion(payload: EventPayload, inner_handles: Set[str]) -> Tuple[str, int, List[str]]:
    user_handle = _normalize_handle(payload.user)
    is_inner = user_handle in inner_handles
    response = payload.response_text

    if payload.action != "RESPOND_PUBLIC":
        addressed = bool(payload.trace.get("director", {}).get("addressed_to_roonie", False))
        trigger = bool(payload.trace.get("director", {}).get("trigger", False))
        reasons = [f"action={payload.action or 'UNKNOWN'}"]
        if not addressed:
            reasons.append("not addressed to roonie")
        if addressed and not trigger:
            reasons.append("addressed but not triggered")
        return ("NOOP", 100, reasons)

    if payload.output_reason and payload.output_reason != "EMITTED":
        reasons = [f"blocked by output gate: {payload.output_reason}"]
        if payload.output_reason in OUTPUT_DISABLED_REASONS:
            reasons.append("runtime safety gate is active")
        return ("BLOCKED", 100, reasons)

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

    if ROAST_HINT_RE.search(response):
        if is_inner:
            reasons.append("mild roast/banter to inner-circle (allowed)")
        else:
            score -= 30
            reasons.append("possible roast tone toward non-inner viewer")

    unknown_emote_like = _detect_unknown_emote_tokens(response, payload.approved_emotes)
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


def _render_line(payload: EventPayload, verdict: str, score: int, reasons: List[str], run_path: Optional[Path]) -> str:
    started = payload.started_at or _utc_now()
    user = payload.user or "unknown"
    run_name = run_path.name if run_path else "unknown"
    reason_text = "; ".join(reasons) if reasons else "on-brand"
    preview = payload.response_text.replace("\n", " ").strip()
    if len(preview) > 240:
        preview = preview[:237].rstrip() + "..."
    return (
        f"[{started}] event={payload.event_id} user=@{user} verdict={verdict} score={score} "
        f"outcome={payload.output_reason or 'UNKNOWN'} file={run_name}\n"
        f"  opinion: {reason_text}\n"
        f"  response: {preview}"
    )


def _render_missing(event_id: str, reason: str, line: str) -> str:
    return (
        f"[{_utc_now()}] event={event_id} verdict=UNKNOWN score=0 outcome={reason}\n"
        f"  opinion: could not load matching run payload yet\n"
        f"  log: {line}"
    )


def _append_output(path: Optional[Path], text: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def _process_signal(
    signal: EventSignal,
    *,
    runs_dir: Path,
    inner_handles: Set[str],
    output_file: Optional[Path],
) -> None:
    run_path = _find_run_file_for_event(runs_dir, signal.event_id, wait_seconds=2.0)
    if run_path is None:
        text = _render_missing(signal.event_id, signal.reason, signal.raw_line)
        print(text, flush=True)
        _append_output(output_file, text)
        return
    payload = _load_event_payload(run_path, signal.event_id)
    if payload is None:
        text = _render_missing(signal.event_id, signal.reason, signal.raw_line)
        print(text, flush=True)
        _append_output(output_file, text)
        return
    verdict, score, reasons = _build_opinion(payload, inner_handles)
    text = _render_line(payload, verdict, score, reasons, run_path)
    print(text, flush=True)
    _append_output(output_file, text)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live watcher that scores each Roonie chat output.")
    parser.add_argument("--control-log", default="logs/control_room.log", help="Path to control_room.log")
    parser.add_argument("--runs-dir", default="runs", help="Directory containing run-v1 JSON files")
    parser.add_argument("--inner-circle", default="data/inner_circle.json", help="Inner-circle data file")
    parser.add_argument("--poll-seconds", type=float, default=0.35, help="Polling interval for log tail")
    parser.add_argument("--backfill-lines", type=int, default=0, help="Evaluate the last N log lines before following")
    parser.add_argument("--output-file", default="logs/roonie_opinions.log", help="Optional output log path")
    parser.add_argument("--max-events", type=int, default=0, help="Stop after N processed events (0 = run forever)")
    parser.add_argument("--show-noop", action="store_true", help="Also print NOOP reasons from processed(no-emit) lines")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    control_log = Path(args.control_log).resolve()
    runs_dir = Path(args.runs_dir).resolve()
    inner_circle_path = Path(args.inner_circle).resolve()
    output_file = Path(args.output_file).resolve() if str(args.output_file).strip() else None

    inner_handles = _extract_inner_circle_handles(inner_circle_path)
    seen_event_ids: Set[str] = set()
    processed_count = 0

    header = (
        f"[{_utc_now()}] roonie-opinion-watcher started "
        f"log={control_log} runs={runs_dir} inner_count={len(inner_handles)}"
    )
    print(header, flush=True)
    _append_output(output_file, header)

    backlog = _read_last_lines(control_log, count=max(0, int(args.backfill_lines)))
    for line in backlog:
        signal = _parse_signal(line)
        if signal is None:
            continue
        if signal.event_id in seen_event_ids:
            continue
        if not args.show_noop and (not signal.emitted) and signal.reason == "NOOP":
            seen_event_ids.add(signal.event_id)
            continue
        seen_event_ids.add(signal.event_id)
        _process_signal(signal, runs_dir=runs_dir, inner_handles=inner_handles, output_file=output_file)
        processed_count += 1
        if args.max_events > 0 and processed_count >= int(args.max_events):
            return 0

    for line in _iter_new_lines(control_log, poll_seconds=max(0.1, float(args.poll_seconds)), start_at_end=True):
        signal = _parse_signal(line)
        if signal is None:
            continue
        if signal.event_id in seen_event_ids:
            continue
        if not args.show_noop and (not signal.emitted) and signal.reason == "NOOP":
            seen_event_ids.add(signal.event_id)
            continue
        seen_event_ids.add(signal.event_id)
        _process_signal(signal, runs_dir=runs_dir, inner_handles=inner_handles, output_file=output_file)
        processed_count += 1
        if args.max_events > 0 and processed_count >= int(args.max_events):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
