"""Environment bootstrapper: dependencies, graph, NPZ scan, .env validation."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[3]
_GRAPH_WAIVER = "waived-by-owner-2026-06-16"


def _reqs_path(repo: Path) -> Path:
    return repo / "apps" / "workbench" / "requirements.txt"


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def check_python(repo: Path) -> Dict[str, Any]:
    ok = sys.version_info >= (3, 12)
    return {
        "python": _python_version(),
        "python_ok": ok,
        "reason": "" if ok else "Python 3.12+ required",
    }


def check_dependencies(repo: Path) -> Dict[str, Any]:
    deps: Dict[str, bool] = {}
    for mod in ("streamlit", "numpy", "pandas", "hftbacktest", "jsonschema"):
        try:
            __import__(mod)
            deps[mod] = True
        except ImportError:
            deps[mod] = False
    missing = [k for k, v in deps.items() if not v]
    return {
        "dependencies": deps,
        "all_present": len(missing) == 0,
        "missing": missing,
        "requirements_path": str(_reqs_path(repo)),
    }


def check_env(repo: Path) -> Dict[str, Any]:
    env_file = repo / ".env"
    has_env = env_file.is_file()
    has_db_key = bool(os.environ.get("DATABENTO_API_KEY"))
    return {
        "env_file_exists": has_env,
        "env_file_path": str(env_file),
        "databento_key_set": has_db_key,
        "env_ok": has_env or has_db_key,
    }


def scan_npz(repo: Path) -> Dict[str, Any]:
    npz_dir = repo / "data" / "npz"
    files: List[str] = []
    total_bytes: int = 0
    if npz_dir.is_dir():
        for f in npz_dir.glob("*.npz"):
            files.append(f.name)
            total_bytes += f.stat().st_size
    return {
        "npz_dir": str(npz_dir),
        "npz_dir_exists": npz_dir.is_dir(),
        "npz_count": len(files),
        "npz_total_size_mb": round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
        "npz_files": files,
    }


def check_graphify(repo: Path) -> Dict[str, Any]:
    graph_json = repo / "graphify-out" / "graph.json"
    root_marker = repo / "graphify-out" / ".graphify_root"
    graph_present = graph_json.is_file() and root_marker.is_file()
    rebuild_cmd = None
    if not graph_present:
        ps1 = repo / "scripts" / "graphify_rebuild.ps1"
        rebuild_cmd = str(ps1) if ps1.is_file() else "graphify update ."
    return {
        "graph_present": graph_present,
        "graph_json_path": str(graph_json),
        "rebuild_command": rebuild_cmd,
    }


def graph_rebuild_waiver(repo: Path) -> Optional[str]:
    if os.environ.get("HFT3_ALLOW_GRAPH_REBUILD_WHILE_WAIVED") == "1":
        return None

    candidates = [
        repo / "runtime" / "vault-gate" / ".last-vault-gate.json",
        repo / "AGENTS.md",
        repo / "docs" / "ai" / "ENGINEERING.md",
        repo / "docs" / "vault" / "AGENT_RUNTIME_ROADMAP.md",
    ]
    vault_root = os.environ.get("HFT3_VAULT_ROOT")
    if vault_root:
        candidates.append(Path(vault_root) / "wiki" / "hot.md")

    for path in candidates:
        if not path.is_file():
            continue
        try:
            if _GRAPH_WAIVER in path.read_text(encoding="utf-8", errors="ignore"):
                return _GRAPH_WAIVER
        except OSError:
            continue
    return None


def rebuild_graph(repo: Path) -> Dict[str, Any]:
    waiver = graph_rebuild_waiver(repo)
    if waiver:
        return {
            "rebuilt": False,
            "skipped": True,
            "waiver": waiver,
            "error": (
                f"graph rebuild owner-waived ({waiver}); "
                "set HFT3_ALLOW_GRAPH_REBUILD_WHILE_WAIVED=1 to override"
            ),
        }

    ps1 = repo / "scripts" / "graphify_rebuild.ps1"
    if ps1.is_file():
        try:
            result = subprocess.run(
                ["powershell", "-File", str(ps1)],
                cwd=str(repo), capture_output=True, text=True, timeout=120,
            )
            ok = result.returncode == 0
            return {"rebuilt": ok, "exit_code": result.returncode, "stderr": result.stderr[:500]}
        except Exception as exc:
            return {"rebuilt": False, "error": str(exc)}
    return {"rebuilt": False, "error": "graphify_rebuild.ps1 not found"}


def setup(repo: Optional[Path] = None, *, rebuild_graph_flag: bool = False) -> Dict[str, Any]:
    repo = repo or _REPO
    result = {
        "repo": str(repo),
        "python": check_python(repo),
        "dependencies": check_dependencies(repo),
        "env": check_env(repo),
        "npz": scan_npz(repo),
        "graphify": check_graphify(repo),
        "all_ok": True,
    }
    if rebuild_graph_flag and not result["graphify"]["graph_present"]:
        result["graph_rebuild"] = rebuild_graph(repo)
    result["all_ok"] = all((
        result["python"]["python_ok"],
        result["dependencies"]["all_present"],
        result["env"]["env_ok"],
    ))
    return result
