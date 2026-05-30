"""Load OpenFoundry connector YAML and vendor pins."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

UPSTREAM_REPO = "https://github.com/syzygyhack/open-foundry"
UPSTREAM_CORE_PACK = Path("domain-packs/core/pack.yaml")

REQUIRED_CONNECTOR_KEYS = (
    "connector_id",
    "schema_version",
    "asset_class",
    "upstream",
    "artifact_root",
    "stream_mappings",
)


def default_connector_path(repo_root: Path) -> Path:
    return repo_root / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml"


def default_vendor_lock_path(repo_root: Path) -> Path:
    return repo_root / "integrations" / "openfoundry" / "VENDOR.lock"


def vendor_root(repo_root: Path) -> Path:
    return repo_root / "vendor" / "openfoundry"


def read_vendor_lock(lock_path: Path) -> Dict[str, str]:
    if not lock_path.is_file():
        return {"openfoundry": "HEAD", "alphageometry": "HEAD"}
    out: Dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def validate_upstream_vendor(repo_root: Path) -> Dict[str, Any]:
    root = vendor_root(repo_root)
    core_pack = root / UPSTREAM_CORE_PACK
    return {
        "upstream_repo": UPSTREAM_REPO,
        "vendor_dir": str(root),
        "vendor_present": root.is_dir(),
        "core_pack_present": core_pack.is_file(),
        "core_pack_path": str(core_pack),
    }


def load_connector(connector_path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(connector_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"connector must be a mapping: {connector_path}")
    missing = [k for k in REQUIRED_CONNECTOR_KEYS if k not in data]
    if missing:
        raise ValueError(f"connector missing required keys {missing}: {connector_path}")
    if data["asset_class"] != "cme_mbo_microstructure":
        raise ValueError(f"unexpected asset_class: {data['asset_class']}")
    upstream = str(data.get("upstream", "")).rstrip("/")
    if upstream != UPSTREAM_REPO:
        raise ValueError(f"connector upstream must be {UPSTREAM_REPO}, got {upstream!r}")
    return data


def validate_connector(repo_root: Path, connector_path: Path | None = None) -> Dict[str, Any]:
    path = connector_path or default_connector_path(repo_root)
    connector = load_connector(path)
    vendor_shas = read_vendor_lock(default_vendor_lock_path(repo_root))
    upstream = validate_upstream_vendor(repo_root)
    return {
        "connector": connector,
        "connector_path": str(path),
        "vendor_shas": vendor_shas,
        "upstream": upstream,
    }
