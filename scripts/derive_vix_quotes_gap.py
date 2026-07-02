"""Derive missing VIX.OPT quotes NPZs from raw DBN (PR-H step 1)."""
import glob, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

def derive(event_id):
    from mbo_release_lane.npz_adapter import derive_npz_from_vix_release
    try:
        out = derive_npz_from_vix_release(REPO, event_id)
        return {"event_id": event_id, "ok": out is not None, "path": str(out) if out else None}
    except Exception as exc:
        return {"event_id": event_id, "ok": False, "error": f"{type(exc).__name__}:{exc}"[:200]}

if __name__ == "__main__":
    raw_events = {p.split(os.sep)[-3] for p in glob.glob(r"C:\hft3-lake\mbo_release\*\VIX.OPT\raw.dbn.zst")}
    quotes = {re.search(r"VIX\.OPT_(.+?)_quotes", os.path.basename(p)).group(1)
              for p in glob.glob(r"C:\hft3-lake\npz\VIX.OPT_*_quotes.npz")}
    missing = sorted(raw_events - quotes)
    print("deriving", len(missing))
    results = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        for r in pool.map(derive, missing, chunksize=4):
            results.append(r)
            if len(results) % 100 == 0:
                print(len(results), "done,", sum(x["ok"] for x in results), "ok", flush=True)
    Path("runtime/vix_receipts").mkdir(parents=True, exist_ok=True)
    json.dump(results, open("runtime/vix_receipts/quotes_derivation.json", "w"), indent=0)
    print("TOTAL", len(results), "OK", sum(x["ok"] for x in results))
