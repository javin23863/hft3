"""Load OpenFoundry connector YAML and vendor pins.

Also validates the hft3 ontology extension citations under
`integrations/openfoundry/domain-packs/hft3/citations/*.yaml`. Every ODL
extension declared in the connector must have a sidecar with a real
`primary.pdf` on disk, a non-empty `primary.section`, a positive
`primary.page`, and at least one `claims[]` entry. See
`docs/research/ONTOLOGY_CITATIONS.md` for the citation table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

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

HFT3_PACK_DIR = Path("integrations/openfoundry/domain-packs/hft3")
HFT3_CITATIONS_DIR = HFT3_PACK_DIR / "citations"
HFT3_REFERENCES_DIR = Path("docs/references")


def default_connector_path(repo_root: Path) -> Path:
    return repo_root / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml"


def default_vendor_lock_path(repo_root: Path) -> Path:
    return repo_root / "integrations" / "openfoundry" / "VENDOR.lock"


def vendor_root(repo_root: Path) -> Path:
    return repo_root / "vendor" / "openfoundry"


def hft3_pack_root(repo_root: Path) -> Path:
    return repo_root / HFT3_PACK_DIR


def hft3_citations_dir(repo_root: Path) -> Path:
    return repo_root / HFT3_CITATIONS_DIR


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
    ontology_citations = validate_ontology_citations(repo_root, connector_path=path)
    return {
        "connector": connector,
        "connector_path": str(path),
        "vendor_shas": vendor_shas,
        "upstream": upstream,
        "ontology_citations": ontology_citations,
    }


def _check_citation_sidecar(
    repo_root: Path, extension: str, sidecar_path: Path, references_dir: Path
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"extension": extension, "sidecar_path": str(sidecar_path), "ok": True, "errors": []}
    if not sidecar_path.is_file():
        entry["ok"] = False
        entry["errors"].append(f"sidecar missing for {extension}: {sidecar_path}")
        return entry
    try:
        data = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        entry["ok"] = False
        entry["errors"].append(f"sidecar YAML parse error: {exc}")
        return entry
    if not isinstance(data, dict):
        entry["ok"] = False
        entry["errors"].append(f"sidecar must be a mapping, got {type(data).__name__}")
        return entry
    if str(data.get("extension", "")).strip() != extension:
        entry["ok"] = False
        entry["errors"].append(
            f"sidecar extension mismatch: declared {extension}, got {data.get('extension')!r}"
        )
    primary = data.get("primary") or {}
    if not isinstance(primary, dict):
        entry["ok"] = False
        entry["errors"].append("sidecar primary must be a mapping")
    else:
        pdf = str(primary.get("pdf", "")).strip()
        section = str(primary.get("section", "")).strip()
        page = primary.get("page")
        if not pdf:
            entry["ok"] = False
            entry["errors"].append("sidecar primary.pdf is empty")
        elif not (references_dir / pdf).is_file():
            entry["ok"] = False
            entry["errors"].append(f"sidecar primary.pdf not on disk: {pdf}")
        if not section:
            entry["ok"] = False
            entry["errors"].append("sidecar primary.section is empty")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            entry["ok"] = False
            entry["errors"].append(f"sidecar primary.page must be a positive int, got {page!r}")
    claims = data.get("claims") or []
    if not isinstance(claims, list) or len(claims) == 0:
        entry["ok"] = False
        entry["errors"].append("sidecar claims must be a non-empty list")
    if entry["errors"]:
        entry["ok"] = False
    return entry


def validate_ontology_citations(
    repo_root: Path,
    connector_path: Path | None = None,
) -> Dict[str, Any]:
    """Verify every ODL extension declared in the connector has a grounded sidecar.

    Returns a dict with `extensions: [{extension, sidecar_path, ok, errors}, ...]`
    and a top-level `all_ok` boolean. Raises no exception; callers decide whether
    to fail closed (e.g. `assert_connector_valid`).
    """
    path = connector_path or default_connector_path(repo_root)
    connector = load_connector(path)
    extensions: List[str] = list(connector.get("ontology_extensions") or [])
    citations_dir = hft3_citations_dir(repo_root)
    references_dir = repo_root / HFT3_REFERENCES_DIR
    entries = [
        _check_citation_sidecar(
            repo_root, ext, citations_dir / f"{ext}.yaml", references_dir
        )
        for ext in extensions
    ]
    return {
        "pack_dir": str(hft3_pack_root(repo_root)),
        "citations_dir": str(citations_dir),
        "references_dir": str(references_dir),
        "extensions": entries,
        "all_ok": all(e["ok"] for e in entries),
    }


def assert_connector_valid(result: Dict[str, Any]) -> None:
    """Fail closed before LLM or KG persist when vendor/connector is incomplete."""
    upstream = result.get("upstream") or {}
    if not upstream.get("vendor_present"):
        raise ValueError("vendor/openfoundry directory missing")
    if not upstream.get("core_pack_present"):
        raise ValueError("OpenFoundry core pack missing under vendor/openfoundry")
    shas = result.get("vendor_shas") or {}
    for key, sha in shas.items():
        token = str(sha).strip().lower()
        if token in ("pending", "head", ""):
            raise ValueError(f"vendor lock {key} not pinned (got {sha!r})")
    citations = result.get("ontology_citations") or {}
    if not citations:
        raise ValueError("ontology_citations missing from validate_connector result (phase 5 regression)")
    if not citations.get("all_ok"):
        bad = [e for e in citations.get("extensions", []) if not e.get("ok")]
        lines = [f"  - {e['extension']}: {'; '.join(e.get('errors', []))}" for e in bad]
        raise ValueError(
            f"ontology citation sidecars failed ({len(bad)} of {len(citations.get('extensions', []))}):\n"
            + "\n".join(lines)
        )
