from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request


def _call(url: str, timeout_s: float) -> tuple[float, dict]:
    start = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return elapsed_ms, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure /api/status latency over repeated calls.")
    parser.add_argument("--url", default="http://127.0.0.1:8787/api/status")
    parser.add_argument("--calls", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-avg-ms", type=float, default=200.0)
    parser.add_argument("--warmup", action="store_true")
    args = parser.parse_args(argv)

    if args.calls < 1:
        print("calls must be >= 1")
        return 2

    if args.warmup:
        try:
            _call(args.url, args.timeout)
        except Exception as exc:  # pragma: no cover - network dependent
            print(f"warmup failed: {exc}")
            return 2

    samples: list[float] = []
    last_payload: dict = {}
    for _ in range(args.calls):
        elapsed_ms, payload = _call(args.url, args.timeout)
        samples.append(elapsed_ms)
        last_payload = payload

    avg_ms = statistics.fmean(samples)
    p95_ms = max(samples) if len(samples) < 20 else statistics.quantiles(samples, n=20)[18]
    print(
        json.dumps(
            {
                "url": args.url,
                "calls": args.calls,
                "avg_ms": round(avg_ms, 2),
                "p95_ms": round(p95_ms, 2),
                "samples_ms": [round(v, 2) for v in samples],
                "armed": bool(last_payload.get("armed")),
                "twitch_connected": bool(last_payload.get("twitch_connected")),
            },
            indent=2,
        )
    )
    if avg_ms > float(args.max_avg_ms):
        print(f"FAIL: average latency {avg_ms:.2f}ms exceeds {args.max_avg_ms:.2f}ms")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
