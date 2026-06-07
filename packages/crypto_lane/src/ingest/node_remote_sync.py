"""Sync btc-node status and mempool gold from remote hosts (chi404 colo)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ingest.paths import ensure_data_dirs, gold_dir
from crypto_lane.src.types import repo_root_from_lane


class NodeRemoteError(RuntimeError):
    pass


def _node_hosts_cfg() -> dict[str, Any]:
    path = repo_root_from_lane() / "packages/crypto_lane/config/node_hosts.yaml"
    return load_yaml(path)


def _cache_dir(repo_root: Path) -> Path:
    d = repo_root / "runtime" / "cache" / "node_hosts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ssh_alias(host_cfg: dict[str, Any]) -> str:
    env_name = str(host_cfg.get("ssh_alias_env", "HFT3_CHI404_SSH"))
    return os.environ.get(env_name, str(host_cfg.get("ssh_alias_default", "chi404"))).strip() or "chi404"


def _remote_repo(host_cfg: dict[str, Any]) -> str:
    env_name = str(host_cfg.get("remote_repo_env", "HFT3_CHI404_REPO"))
    return os.environ.get(env_name, str(host_cfg.get("remote_repo_default", "/root/hft3/repo"))).strip()


def ssh_probe(alias: str, *, timeout_s: int = 12) -> bool:
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={min(8, timeout_s)}",
                alias,
                "echo ok",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.returncode == 0 and "ok" in proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def ssh_cat(alias: str, remote_path: str, *, timeout_s: int = 20) -> str | None:
    if ";" in remote_path or "|" in remote_path or "`" in remote_path:
        raise NodeRemoteError(f"unsafe remote path: {remote_path!r}")
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={min(8, timeout_s)}",
                alias,
                f"cat {remote_path}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _ssh_ls_glob(alias: str, remote_glob: str, *, timeout_s: int = 30) -> list[str]:
    if any(c in remote_glob for c in ";|&`$"):
        raise NodeRemoteError(f"unsafe remote glob: {remote_glob!r}")
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={min(8, timeout_s)}",
                alias,
                f"ls -1 {remote_glob} 2>/dev/null || true",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def sync_chi404_btc_node_artifacts(*, repo_root: Path | None = None) -> dict[str, Any]:
    """Fetch chi404 btc-node status, .btc-node.env, and mempool jsonl into local cache."""
    root = repo_root or repo_root_from_lane()
    cfg = _node_hosts_cfg()
    host_cfg = cfg.get("hosts", {}).get("chi404", {})
    alias = _ssh_alias(host_cfg)
    remote_repo = _remote_repo(host_cfg)
    cache = _cache_dir(root)
    report: dict[str, Any] = {
        "host": "chi404",
        "ssh_alias": alias,
        "remote_repo": remote_repo,
        "reachable": False,
        "status_synced": False,
        "btc_node_env_synced": False,
        "mempool_files_synced": 0,
        "errors": [],
    }
    if os.environ.get("HFT3_CHI404_NODE_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        report["skipped"] = "HFT3_CHI404_NODE_ENABLED=0"
        return report

    if not ssh_probe(alias):
        report["errors"].append(f"ssh probe failed for {alias}")
        return report
    report["reachable"] = True

    for rel in host_cfg.get("status_paths") or []:
        remote = f"{remote_repo}/{rel}".replace("//", "/")
        raw = ssh_cat(alias, remote)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out = cache / "chi404-btc-node-status.json"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        report["status_synced"] = True
        report["status_path"] = str(out)
        report["status_source"] = remote
        break

    for rel in host_cfg.get("btc_node_env_paths") or []:
        remote = f"{remote_repo}/{rel}".replace("//", "/")
        raw = ssh_cat(alias, remote)
        if not raw:
            continue
        out = cache / "chi404.btc-node.env"
        out.write_text(raw, encoding="utf-8")
        try:
            out.chmod(0o600)
        except OSError:
            pass
        report["btc_node_env_synced"] = True
        report["btc_node_env_path"] = str(out)
        break

    mempool_rel = str(host_cfg.get("mempool_gold_dir", "data/crypto/gold/bitcoind/mempool"))
    remote_dir = f"{remote_repo}/{mempool_rel}".replace("//", "/")
    remote_files = _ssh_ls_glob(alias, f"{remote_dir}/*_mempool_snapshot.jsonl")
    ensure_data_dirs()
    local_dir = gold_dir() / "bitcoind" / "mempool"
    local_dir.mkdir(parents=True, exist_ok=True)
    for remote_file in remote_files:
        name = Path(remote_file).name
        local_path = local_dir / name
        try:
            proc = subprocess.run(
                [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={min(8, 30)}",
                    f"{alias}:{remote_file}",
                    str(local_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0 and local_path.is_file():
                report["mempool_files_synced"] = int(report["mempool_files_synced"]) + 1
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["errors"].append(f"scp {name}: {exc}")

    return report


def local_mempool_jsonl_days(*, start: str, end: str) -> list[str]:
    """Days with local bitcoind mempool jsonl gold (incl. chi404 sync)."""
    root = gold_dir() / "bitcoind" / "mempool"
    if not root.is_dir():
        return []
    days: list[str] = []
    for path in root.glob("*_mempool_snapshot.jsonl"):
        day = path.name[:10]
        if len(day) == 10 and start <= day <= end:
            days.append(day)
    return sorted(set(days))
