from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Sequence


DEFAULT_ALLOWED_KEYS: tuple[str, ...] = (
    "tone_preferences",
    "stream_norms",
    "approved_phrases",
    "do_not_do",
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._\-]{8,}\b", re.IGNORECASE)
_OAUTH_RE = re.compile(r"\boauth:[A-Za-z0-9._\-]{8,}\b", re.IGNORECASE)
_TOKEN_ASSIGN_RE = re.compile(
    r"\b(?:token|secret|api[_\-]?key)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]", "", text)
    return text


def _decode_tags(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [_normalize_key(item) for item in raw if _normalize_key(item)]
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_normalize_key(item) for item in parsed if _normalize_key(item)]
    except json.JSONDecodeError:
        pass
    return [_normalize_key(part) for part in text.split(",") if _normalize_key(part)]


def _contains_pii(text: str) -> bool:
    value = str(text or "")
    return bool(
        _EMAIL_RE.search(value)
        or _IPV4_RE.search(value)
        or _BEARER_RE.search(value)
        or _OAUTH_RE.search(value)
        or _TOKEN_ASSIGN_RE.search(value)
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_db_path() -> Path:
    configured = str(os.environ.get("ROONIE_MEMORY_DB_PATH", "")).strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = (_repo_root() / configured).resolve()
        return path
    dashboard_data_dir = str(os.environ.get("ROONIE_DASHBOARD_DATA_DIR", "")).strip()
    if dashboard_data_dir:
        return (Path(dashboard_data_dir) / "memory.sqlite").resolve()
    return (_repo_root() / "data" / "memory.sqlite").resolve()


@dataclass(frozen=True)
class SafeInjectionResult:
    text_snippet: str
    keys_used: List[str]
    chars_used: int
    items_used: int
    dropped_count: int


def get_safe_injection(
    db_path: Path | str | None = None,
    *,
    max_chars: int = 900,
    max_items: int = 10,
    allowed_keys: Sequence[str] = DEFAULT_ALLOWED_KEYS,
) -> SafeInjectionResult:
    """
    Read-only memory injection for ProviderDirector prompt shaping.
    - Uses only whitelisted tags (keys).
    - Drops candidate items that match basic PII/token patterns.
    - Applies deterministic item/char caps.
    """
    lim_chars = max(0, int(max_chars))
    lim_items = max(0, int(max_items))
    if lim_chars == 0 or lim_items == 0:
        return SafeInjectionResult("", [], 0, 0, 0)

    normalized_allowed: List[str] = []
    seen_allowed: set[str] = set()
    for key in allowed_keys:
        norm = _normalize_key(key)
        if norm and norm not in seen_allowed:
            normalized_allowed.append(norm)
            seen_allowed.add(norm)
    if not normalized_allowed:
        return SafeInjectionResult("", [], 0, 0, 0)

    path = Path(db_path).resolve() if db_path is not None else _default_db_path()
    if not path.exists():
        return SafeInjectionResult("", [], 0, 0, 0)

    rows: list[sqlite3.Row] = []
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT note, tags, updated_at, created_at
                FROM cultural_notes
                WHERE is_active = 1
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 200
                """
            ).fetchall()
    except sqlite3.Error:
        return SafeInjectionResult("", [], 0, 0, 0)

    keys_used: List[str] = []
    lines: List[str] = []
    dropped_count = 0

    for row in rows:
        if len(lines) >= lim_items:
            break
        note = str(row["note"] or "").strip()
        if not note:
            continue
        if _contains_pii(note):
            dropped_count += 1
            continue
        row_tags = _decode_tags(row["tags"])
        matched_key = next((key for key in normalized_allowed if key in row_tags), "")
        if not matched_key:
            continue
        line = f"- {matched_key}: {note}"
        current = "\n".join(lines)
        separator = "\n" if current else ""
        candidate = f"{current}{separator}{line}"
        if len(candidate) > lim_chars:
            remaining = lim_chars - len(current) - (1 if current else 0)
            if remaining <= 0:
                break
            if remaining <= 3:
                truncated = line[:remaining]
            else:
                truncated = line[: remaining - 3].rstrip() + "..."
            if truncated:
                lines.append(truncated)
                if matched_key not in keys_used:
                    keys_used.append(matched_key)
            break
        lines.append(line)
        if matched_key not in keys_used:
            keys_used.append(matched_key)

    snippet = "\n".join(lines)
    return SafeInjectionResult(
        text_snippet=snippet,
        keys_used=keys_used,
        chars_used=len(snippet),
        items_used=len(lines),
        dropped_count=dropped_count,
    )


__all__ = ["SafeInjectionResult", "get_safe_injection", "DEFAULT_ALLOWED_KEYS"]
