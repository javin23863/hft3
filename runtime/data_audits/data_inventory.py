#!/usr/bin/env python3
"""Single unified data inventory: repo, paid root, AND remote chi404."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths
setup_repo_paths()

from data_system.src.data_roots import paid_data_root


def count_npz_near(dirpath: Path) -> tuple[int, int]:
    """(file_count, total_bytes)"""
    count = 0
    total = 0
    if not dirpath.is_dir():
        return (0, 0)
    for p in dirpath.glob("*.npz"):
        count += 1
        total += p.stat().st_size
    return (count, total)


def count_raw_dbn(dirpath: Path) -> tuple[int, int]:
    count = 0
    total = 0
    if not dirpath.is_dir():
        return (0, 0)
    for p in dirpath.rglob("*.dbn*"):
        count += 1
        total += p.stat().st_size
    return (count, total)


def count_any(dirpath: Path, pattern: str, recursive: bool = False) -> tuple[int, int]:
    count = 0
    total = 0
    if not dirpath.is_dir():
        return (0, 0)
    for p in dirpath.rglob(pattern) if recursive else dirpath.glob(pattern):
        count += 1
        total += p.stat().st_size
    return (count, total)


def main() -> int:
    repo = _REPO
    paid = paid_data_root(repo)

    inv: dict = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "paid_data_root": str(paid),
        "locations": {},
        "totals": {},
    }

    # 1. Repo data/
    repo_data = repo / "data"
    r_npz_cnt, r_npz_sz = count_npz_near(repo_data / "npz")
    r_raw_cnt, r_raw_sz = count_raw_dbn(repo_data)
    inv["locations"]["repo_data_npz"] = {
        "path": str(repo_data / "npz"),
        "npz_files": r_npz_cnt,
        "npz_bytes": r_npz_sz,
        "npz_gb": round(r_npz_sz / 1e9, 2),
    }
    inv["locations"]["repo_data_raw_dbn"] = {
        "path": str(repo_data),
        "raw_dbn_files": r_raw_cnt,
        "raw_dbn_bytes": r_raw_sz,
        "raw_dbn_gb": round(r_raw_sz / 1e9, 2),
    }
    # equities
    eq_npz_cnt, eq_npz_sz = count_npz_near(repo_data / "equities" / "npz")
    inv["locations"]["repo_equities_npz"] = {
        "path": str(repo_data / "equities" / "npz"),
        "npz_files": eq_npz_cnt,
        "npz_bytes": eq_npz_sz,
        "npz_gb": round(eq_npz_sz / 1e9, 2),
    }

    # 2. Paid data root
    p_npz_cnt, p_npz_sz = count_npz_near(paid / "npz")
    p_raw_cnt, p_raw_sz = count_raw_dbn(paid / "raw")
    inv["locations"]["paid_data_npz"] = {
        "path": str(paid / "npz"),
        "npz_files": p_npz_cnt,
        "npz_bytes": p_npz_sz,
        "npz_gb": round(p_npz_sz / 1e9, 2),
    }
    inv["locations"]["paid_data_raw_dbn"] = {
        "path": str(paid / "raw"),
        "raw_dbn_files": p_raw_cnt,
        "raw_dbn_bytes": p_raw_sz,
        "raw_dbn_gb": round(p_raw_sz / 1e9, 2),
    }

    # 3. chi404 remote
    try:
        r = subprocess.run(
            ["ssh", "chi404", "find /root/hft3/repo/data/npz -name '*.npz' | wc -l; du -sb /root/hft3/repo/data/npz/ 2>/dev/null | cut -f1; find /root/hft3/repo/data -name '*.dbn*' | wc -l; ls -l /root/hft3/repo/data/manifest.parquet 2>/dev/null | awk '{print \$5,\$6,\$7,\$8}'; ps aux | grep download_mbo | grep -v grep | awk '{print \$2,\$8,\$9}'"],
            capture_output=True, text=True, timeout=15,
        )
        lines = r.stdout.strip().split("\n")
        c_npz_cnt = int(lines[0]) if len(lines) > 0 else 0
        c_npz_sz = int(lines[1]) if len(lines) > 1 else 0
        c_raw_cnt = int(lines[2]) if len(lines) > 2 else 0
        c_manifest = lines[3] if len(lines) > 3 else ""
        c_procs = lines[4] if len(lines) > 4 else ""
        inv["locations"]["chi404_npz"] = {
            "path": "/root/hft3/repo/data/npz",
            "npz_files": c_npz_cnt,
            "npz_bytes": c_npz_sz,
            "npz_gb": round(c_npz_sz / 1e9, 2),
        }
        inv["locations"]["chi404_raw_dbn"] = {
            "path": "/root/hft3/repo/data/",
            "raw_dbn_files": c_raw_cnt,
        }
        inv["locations"]["chi404_manifest"] = {
            "info": c_manifest,
        }
        inv["locations"]["chi404_processes"] = {
            "download_mbo": c_procs,
        }
    except Exception as ex:
        inv["locations"]["chi404"] = {"error": str(ex)}

    # 4. Totals
    total_npz = r_npz_cnt + eq_npz_cnt + p_npz_cnt
    total_npz_unique_pairs = None
    # Compute combined unique (sym, eid) across repo + paid NPZ
    cme_syms = {"MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"}

    def parse_all(dirpath: Path, valid_syms: set | None = None) -> set:
        pairs = set()
        if not dirpath.is_dir():
            return pairs
        for p in dirpath.glob("*.npz"):
            name = p.stem
            parts = name.split("_")
            if len(parts) < 5:
                continue
            sym = parts[0]
            if valid_syms and sym not in valid_syms:
                continue
            yyyy_idx = None
            for i, part in enumerate(parts):
                if re.match(r"^\d{4}$", part):
                    yyyy_idx = i
                    break
            if yyyy_idx is None or yyyy_idx + 3 >= len(parts):
                continue
            date = f"{parts[yyyy_idx]}_{parts[yyyy_idx+1]}_{parts[yyyy_idx+2]}"
            window = parts[yyyy_idx + 3]
            eid = "_".join(parts[1:yyyy_idx] + [date, window])
            pairs.add((sym, eid))
        return pairs

    repo_cme = parse_all(repo_data / "npz", cme_syms)
    paid_cme = parse_all(paid / "npz", cme_syms)
    combined_cme = repo_cme | paid_cme

    inv["totals"] = {
        "all_npz_files": total_npz,
        "all_npz_bytes": r_npz_sz + eq_npz_sz + p_npz_sz,
        "all_npz_gb": round((r_npz_sz + eq_npz_sz + p_npz_sz) / 1e9, 2),
        "cme_unique_sym_eid_pairs": len(combined_cme),
        "repo_cme_pairs": len(repo_cme),
        "paid_cme_pairs": len(paid_cme),
        "paid_cme_pairs_not_in_repo": len(paid_cme - repo_cme),
    }

    # 5. Readiness from latest fast audit
    fast_audit_path = repo / "runtime" / "data_audits" / "all_models_symbols_backtest_fast.json"
    if fast_audit_path.is_file():
        fa = json.loads(fast_audit_path.read_text(encoding="utf-8"))
        inv["cme_readiness"] = fa.get("overall")
        inv["model_pct_distribution"] = fa.get("model_pct_distribution")

    out_path = repo / "runtime" / "data_audits" / "data_inventory.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(inv, indent=2), encoding="utf-8")
    print(json.dumps(inv, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
