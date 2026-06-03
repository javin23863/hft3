"""Fetch free daily OHLCV from Yahoo Finance v8 chart endpoint.

Reads seed config, fetches each positive seed ticker from Yahoo v8 chart,
and writes one CSV per ticker into data/equities/daily/{TICKER}.csv in the
DailyBar-compatible format (symbol, date, open, high, low, close, volume).

This is a one-shot helper for the free-daily phase. It does NOT touch L2/L3.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent
REPO = WORKTREE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages"))

from equities_lane.src.prediction.runner_seed_resolver import load_seed_config, load_seed_tickers  # noqa: E402

USER_AGENT = "hft3-free-daily-fetch/1.0 (+research, no_paid_keys)"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={p1}&period2={p2}&interval=1d&events=history"


def http_get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - controlled URL
        return json.loads(resp.read().decode("utf-8"))


def fetch_chart(symbol: str, start: str, end: str) -> dict:
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol), p1=p1, p2=p2)
    return http_get_json(url)


def chart_to_bars(symbol: str, payload: dict) -> list[dict]:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    block = result[0]
    ts = block.get("timestamp") or []
    ind = block.get("indicators") or {}
    quotes = (ind.get("quote") or [{}])[0]
    adj = (ind.get("adjclose") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []
    adjclose = adj.get("adjclose") or []
    bars: list[dict] = []
    for i, sec in enumerate(ts):
        try:
            o = opens[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]
            v = volumes[i]
            a = adjclose[i] if i < len(adjclose) else None
        except IndexError:
            continue
        if o is None or h is None or l is None or c is None or v is None:
            continue
        date_str = datetime.fromtimestamp(sec, tz=timezone.utc).date().isoformat()
        bars.append({
            "symbol": symbol,
            "date": date_str,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
            "adjclose": float(a) if a is not None else float(c),
        })
    return bars


def write_csv(path: Path, bars: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["symbol", "date", "open", "high", "low", "close", "volume", "adjclose"]
    lines = [",".join(header)]
    for b in bars:
        row = [b[h] for h in header]
        lines.append(",".join(str(v) for v in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    config_path = REPO / "packages" / "equities_lane" / "config" / "historical_runner_benchmark.yaml"
    out_dir = REPO / "data" / "equities" / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_seed_config(config_path)
    paths = cfg.get("paths") or {}
    start = str(paths.get("free_daily_window_start", "2022-01-01"))
    end = str(paths.get("free_daily_window_end", "2026-01-01"))
    seeds = load_seed_tickers(config_path)
    print(f"Fetching {len(seeds)} tickers from Yahoo v8 chart ({start} to {end})", flush=True)

    summary: list[dict] = []
    for i, seed in enumerate(seeds, start=1):
        out_path = out_dir / f"{seed.ticker}.csv"
        try:
            payload = fetch_chart(seed.ticker, start, end)
        except Exception as exc:  # noqa: BLE001
            summary.append({"ticker": seed.ticker, "ok": False, "error": f"fetch_failed: {exc!r}"})
            print(f"[{i:>2}/{len(seeds)}] {seed.ticker:<6} fetch_failed: {exc!r}", flush=True)
            time.sleep(0.3)
            continue

        bars = chart_to_bars(seed.ticker, payload)
        if not bars:
            summary.append({"ticker": seed.ticker, "ok": False, "error": "no_bars_returned"})
            print(f"[{i:>2}/{len(seeds)}] {seed.ticker:<6} no_bars_returned", flush=True)
            time.sleep(0.2)
            continue

        write_csv(out_path, bars)
        summary.append({"ticker": seed.ticker, "ok": True, "n_bars": len(bars), "path": str(out_path)})
        print(f"[{i:>2}/{len(seeds)}] {seed.ticker:<6} ok {len(bars)} bars -> {out_path}", flush=True)
        time.sleep(0.2)

    (out_dir / "_fetch_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    n_ok = sum(1 for s in summary if s.get("ok"))
    print(f"\nDone. {n_ok}/{len(seeds)} tickers fetched. Summary at {out_dir / '_fetch_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
