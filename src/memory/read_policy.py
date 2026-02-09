from __future__ import annotations

from typing import Any, Dict, List


def _get_nested(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def apply_memory_read_policy(
    *,
    store: Dict[str, Any],
    viewer_key: str,
    explicit_context: bool,
    requested_slots: List[str],
) -> Dict[str, Any]:
    """
    Phase 10I: Deterministic memory reads with non-creepy policy.

    Rules:
      - If explicit_context is False: do not use memory at all.
      - Preference suppression: never include preferences.dislikes in included output.
        (Even if explicitly requested.) Dislikes are returned under 'suppressed' for auditability.
      - Only return keys explicitly requested (slot-based).
      - Missing viewer or missing slot -> ignored (not included, not suppressed).
    """
    if not explicit_context:
        return {"used": False, "included": {}, "suppressed": {}}

    viewer = store.get(viewer_key, {})
    included: Dict[str, Any] = {}
    suppressed: Dict[str, Any] = {}

    for slot in requested_slots:
        slot_norm = str(slot).strip()
        if not slot_norm:
            continue

        value = _get_nested(viewer, slot_norm)
        if value is None:
            continue

        # Hard suppression rule for dislikes
        if slot_norm == "preferences.dislikes":
            suppressed[slot_norm] = value
            continue

        included[slot_norm] = value

    return {"used": True, "included": included, "suppressed": suppressed}
