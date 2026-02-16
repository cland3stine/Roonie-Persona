from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SAFE_EVENTSUB_FIELDS = {
    "ts",
    "twitch_event_id",
    "event_type",
    "session_id",
    "emitted",
    "suppression_reason",
}

DISALLOWED_FIELD_TOKENS = {
    "email",
    "ip",
    "user_login",
    "display_name",
    "user_name",
    "token",
    "oauth",
    "access_token",
    "refresh_token",
    "raw_payload",
    "payload",
    "raw",
}

PII_PATTERNS: Dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "oauth_token": re.compile(r"\boauth:[A-Za-z0-9_\-]{8,}\b", re.IGNORECASE),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.IGNORECASE),
}


def _iter_string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_string_values(item)


def _scan_strings_for_pii(texts: Iterable[str]) -> List[str]:
    hits: List[str] = []
    for text in texts:
        for label, rx in PII_PATTERNS.items():
            if rx.search(text or ""):
                hits.append(label)
    return sorted(set(hits))


def validate_eventsub_entry(entry: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    keys = set(entry.keys())
    unexpected = sorted(k for k in keys if k not in SAFE_EVENTSUB_FIELDS)
    if unexpected:
        issues.append(f"unexpected_fields:{','.join(unexpected)}")
    lowered_keys = {str(k).strip().lower() for k in keys}
    disallowed_fields = sorted(
        key for key in lowered_keys if key in DISALLOWED_FIELD_TOKENS or any(tok in key for tok in DISALLOWED_FIELD_TOKENS)
    )
    if disallowed_fields:
        issues.append(f"disallowed_fields:{','.join(disallowed_fields)}")
    pii_hits = _scan_strings_for_pii(_iter_string_values(entry))
    if pii_hits:
        issues.append(f"pii_patterns:{','.join(pii_hits)}")
    return issues


def _read_recent_lines(path: Path, max_lines: int) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if max_lines > 0:
        return lines[-max_lines:]
    return lines


def scan_log_paths(paths: List[Path], *, max_lines: int = 1000) -> Dict[str, Any]:
    violations: List[Dict[str, Any]] = []
    scanned_lines = 0
    for path in paths:
        lines = _read_recent_lines(path, max_lines=max_lines)
        for line_no, line in enumerate(lines, start=1):
            scanned_lines += 1
            stripped = str(line or "").strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                pii_hits = _scan_strings_for_pii([stripped])
                if pii_hits:
                    violations.append(
                        {
                            "file": str(path),
                            "line": line_no,
                            "issue": f"pii_patterns:{','.join(pii_hits)}",
                        }
                    )
                continue

            entry_issues: List[str] = []
            if path.name == "eventsub_events.jsonl":
                entry_issues.extend(validate_eventsub_entry(entry if isinstance(entry, dict) else {}))
            else:
                pii_hits = _scan_strings_for_pii(_iter_string_values(entry))
                if pii_hits:
                    entry_issues.append(f"pii_patterns:{','.join(pii_hits)}")
            for issue in entry_issues:
                violations.append(
                    {
                        "file": str(path),
                        "line": line_no,
                        "issue": issue,
                    }
                )
    return {
        "files_scanned": [str(p) for p in paths],
        "lines_scanned": scanned_lines,
        "violations": violations,
    }


def default_log_paths(logs_dir: Path) -> List[Path]:
    out: List[Path] = []
    for name in ("eventsub_events.jsonl", "operator_audit.jsonl"):
        path = logs_dir / name
        if path.exists():
            out.append(path)
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roonie log hygiene check (PII and schema).")
    parser.add_argument("--logs-dir", default="logs", help="Directory containing log files.")
    parser.add_argument("--max-lines", type=int, default=1000, help="Max recent lines per file to scan.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logs_dir = Path(args.logs_dir)
    paths = default_log_paths(logs_dir)
    report = scan_log_paths(paths, max_lines=max(1, int(args.max_lines)))

    print(f"Scanned files: {len(report['files_scanned'])}, lines: {report['lines_scanned']}")
    violations = report["violations"]
    if not violations:
        print("Log hygiene: OK (0 hits)")
        return 0
    print(f"Log hygiene: FAIL ({len(violations)} issue(s))")
    for row in violations[:50]:
        print(f"- {row['file']}:{row['line']} {row['issue']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
