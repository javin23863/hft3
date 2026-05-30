"""Parse cyclictest histogram files into latency percentiles (microseconds)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_histogram(path: Path) -> list[tuple[int, int]]:
    buckets: list[tuple[int, int]] = []
    if not path.is_file():
        return buckets
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            usec = int(parts[0])
            count = int(parts[1])
        except ValueError:
            continue
        buckets.append((usec, count))
    return buckets


def cumulative_percentile_us(buckets: list[tuple[int, int]], percentile: float) -> int | None:
    """Match infrastructure/chi404/05_jitter_gate.sh cumulative-bucket walk."""
    total = sum(c for _, c in buckets)
    if total == 0:
        return None
    target = int(total * percentile)
    running = 0
    result = buckets[-1][0]
    for usec, count in buckets:
        running += count
        if running >= target:
            result = usec
            break
    return int(result)


def percentiles_us(buckets: list[tuple[int, int]]) -> dict[str, int | None]:
    total = sum(c for _, c in buckets)
    if total == 0:
        return {
            "p50_us": None,
            "p95_us": None,
            "p99_us": None,
            "p999_us": None,
            "max_us": None,
            "samples": 0,
        }

    return {
        "p50_us": cumulative_percentile_us(buckets, 0.50),
        "p95_us": cumulative_percentile_us(buckets, 0.95),
        "p99_us": cumulative_percentile_us(buckets, 0.99),
        "p999_us": cumulative_percentile_us(buckets, 0.999),
        "max_us": int(buckets[-1][0]),
        "samples": total,
    }


def summarize_hist_file(path: Path) -> dict[str, int | None]:
    return percentiles_us(load_histogram(path))


def main() -> None:
    path = Path(sys.argv[1])
    print(json.dumps(summarize_hist_file(path), indent=2))


if __name__ == "__main__":
    main()
