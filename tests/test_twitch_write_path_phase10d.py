from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TwitchGate:
    enabled: bool
    armed: bool
    kill_switch: bool
    mode: str  # "live" or "replay"


def _parse_fixture_gate(path: Path) -> TwitchGate:
    """
    Minimal TOML parser for our fixtures only.
    Avoids introducing new dependencies. Expected format:

    [twitch]
    enabled = true/false
    armed = true/false
    kill_switch = true/false
    mode = "live"/"replay"
    """
    txt = path.read_text(encoding="utf-8").splitlines()

    in_twitch = False
    kv = {}
    for raw in txt:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[twitch]":
            in_twitch = True
            continue
        if line.startswith("[") and line.endswith("]") and line != "[twitch]":
            in_twitch = False
            continue
        if not in_twitch:
            continue
        if "=" not in line:
            continue
        k, v = [x.strip() for x in line.split("=", 1)]
        kv[k] = v

    def _bool(s: str) -> bool:
        s = s.lower()
        if s == "true":
            return True
        if s == "false":
            return False
        raise ValueError(f"Invalid boolean: {s}")

    mode = kv["mode"].strip().strip('"').strip("'")
    return TwitchGate(
        enabled=_bool(kv["enabled"]),
        armed=_bool(kv["armed"]),
        kill_switch=_bool(kv["kill_switch"]),
        mode=mode,
    )


def test_twitch_write_path_gating_and_replay_never_posts(tmp_path: Path):
    """
    Phase 10D invariants:
      - Not armed -> never posts
      - Kill switch ON -> never posts
      - Live + armed + kill switch OFF -> posts exactly once
      - Replay -> never posts, even if armed and kill switch OFF
    """
    from src.twitch.write_path import maybe_post_nowplaying

    # Simulated nowplaying output (what 10C writes for streamer.bot)
    msg = "Now Playing: Artist A - Track One (Released 2020 on Label One)"

    posted: list[str] = []

    def fake_post_fn(text: str) -> None:
        posted.append(text)

    fx_dir = Path("tests/fixtures/v1_10d_twitch_write_path")

    cases = [
        ("case_not_armed.toml", 0),
        ("case_kill_switch_on.toml", 0),
        ("case_live_post_allowed.toml", 1),
        ("case_replay_never_posts.toml", 0),
    ]

    for fname, expected_posts in cases:
        posted.clear()
        gate = _parse_fixture_gate(fx_dir / fname)
        maybe_post_nowplaying(
            gate_enabled=gate.enabled,
            gate_armed=gate.armed,
            kill_switch=gate.kill_switch,
            mode=gate.mode,
            message=msg,
            post_fn=fake_post_fn,
        )
        assert len(posted) == expected_posts
        if expected_posts == 1:
            assert posted[0] == msg


def test_kill_switch_default_is_on():
    """
    Kill switch default ON is a hard safety invariant.
    """
    from src.twitch.write_path import DEFAULT_KILL_SWITCH_ON

    assert DEFAULT_KILL_SWITCH_ON is True
