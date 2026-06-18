"""Ontology Gate Agent — deterministic fail-closed checkpoint for hft3.

Implements the gate described in docs/project/ONTOLOGY_GATE_AGENT_SPEC.md. The
gate is deliberately non-LLM: every check is a rule over concrete inputs. The
module delegates artifact schema validation to the existing validators in
``backtest_pipeline.src.vectorbt_adapter`` and
``backtest_pipeline.src.feature_plane`` rather than duplicating them.

Public surface:

- :class:`FableChecklist` / :func:`validate_fable_entry_checklist`
- :class:`CitationResult` / :func:`trace_citation`
- :class:`InvariantResult` / :func:`check_invariants`
- :class:`ToolUsageResult` / :func:`check_tool_usage`
- :class:`ArtifactResult` / :func:`validate_artifact_schema`
- :class:`DriftResult` / :func:`check_drift`
- :class:`ScopeHonestyResult` / :func:`check_scope_honesty`
- :class:`GateVerdict` / :func:`gate_decision` / :func:`run_gate`

Authority references are resolved against the real filesystem paths:

* vault papers: ``<vault>/hft3/library/papers/*.md`` (filename without ``.md``
  is the ``paper_id``)
* repo authority docs: ``docs/project/*.md``, ``specs/*.md``
* vendor locks: ``vendor/vectorbt/VENDOR.lock``, ``vendor/hftbacktest/VENDOR.lock``
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backtest_pipeline.src.feature_plane import (
    FEATURE_PLANE_STATUSES,
    feature_plane_validation_errors,
)
from backtest_pipeline.src.vectorbt_adapter import (
    SCREENING_ARTIFACT_REQUIRED_FIELDS,
    validate_screening_artifact,
)


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCS_PROJECT = _REPO_ROOT / "docs" / "project"
_DOCS_ROOT = _REPO_ROOT / "docs"
_SPECS_ROOT = _REPO_ROOT / "specs"
_VENDOR_VBT_LOCK = _REPO_ROOT / "vendor" / "vectorbt" / "VENDOR.lock"
_VENDOR_HBT_LOCK = _REPO_ROOT / "vendor" / "hftbacktest" / "VENDOR.lock"

_env_vault = os.environ.get("HFT3_VAULT_ROOT", "").strip()
if _env_vault:
    _DEFAULT_VAULT_ROOT = Path(_env_vault)
elif os.environ.get("USERPROFILE"):
    _DEFAULT_VAULT_ROOT = Path(os.environ["USERPROFILE"]) / "Desktop" / "Obsidian Vault From VPS" / "hft3"
else:
    # Fallback: check common vault locations relative to repo root and home dir.
    _candidate = _REPO_ROOT.parent / "Desktop" / "Obsidian Vault From VPS" / "hft3"
    if _candidate.is_dir():
        _DEFAULT_VAULT_ROOT = _candidate
    else:
        _DEFAULT_VAULT_ROOT = Path.home() / "Desktop" / "Obsidian Vault From VPS" / "hft3"
_VAULT_LIBRARY_PAPERS = _DEFAULT_VAULT_ROOT / "library" / "papers"


def vault_papers_dir() -> Path:
    """Directory holding ``library/papers/*.md`` in the hft3 vault."""
    override = os.environ.get("ONTOLOGY_GATE_VAULT_PAPERS")
    if override:
        return Path(override)
    return _VAULT_LIBRARY_PAPERS


def repo_docs_project_dir() -> Path:
    return _DOCS_PROJECT


def repo_specs_dir() -> Path:
    return _SPECS_ROOT


def repo_docs_dir() -> Path:
    return _DOCS_ROOT


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

RED = "red"
YELLOW = "yellow"
GREEN = "green"


def _severity(level: str) -> str:
    return {"red": RED, "yellow": YELLOW, "green": GREEN}.get(level, level)


# ---------------------------------------------------------------------------
# 1. Fable entry checklist
# ---------------------------------------------------------------------------

FABLE_CHECKBOXES: Sequence[str] = (
    "GROUNDED",
    "VAULT_READ",
    "AUTHORITY_LOCATED",
    "NO_ASSUMPTIONS",
    "FABLE_ACTIVE",
)


@dataclass(frozen=True)
class FableChecklist:
    """Result of the 5-checkbox Fable entry checklist (spec §0C)."""

    grounded: bool
    vault_read: bool
    authority_located: bool
    no_assumptions: bool
    fable_active: bool

    @property
    def all_true(self) -> bool:
        return all(
            (self.grounded, self.vault_read, self.authority_located, self.no_assumptions, self.fable_active)
        )

    def as_dict(self) -> dict[str, bool]:
        return {
            "GROUNDED": self.grounded,
            "VAULT_READ": self.vault_read,
            "AUTHORITY_LOCATED": self.authority_located,
            "NO_ASSUMPTIONS": self.no_assumptions,
            "FABLE_ACTIVE": self.fable_active,
        }


def validate_fable_entry_checklist(
    *,
    grounded: bool,
    vault_read: bool,
    authority_located: bool,
    no_assumptions: bool,
    fable_active: bool,
) -> FableChecklist:
    """Validate the 5 mandatory Fable entry checkboxes.

    If any checkbox is false the gate must not proceed (spec §0C). The returned
    object carries the booleans; callers check ``.all_true``.
    """
    return FableChecklist(
        grounded=bool(grounded),
        vault_read=bool(vault_read),
        authority_located=bool(authority_located),
        no_assumptions=bool(no_assumptions),
        fable_active=bool(fable_active),
    )


# ---------------------------------------------------------------------------
# 2. Citation tracer
# ---------------------------------------------------------------------------

SOURCE_PAPER = "paper"
SOURCE_SPEC = "spec"
SOURCE_TOOL_DOC = "tool_doc"
SOURCE_UNBACKED = "unbacked"


@dataclass(frozen=True)
class CitationResult:
    """Result of :func:`trace_citation`.

    ``backed`` is True when the claim resolves to a real paper/spec/tool doc.
    ``source_ref`` is the concrete path or identifier that was found.
    """

    backed: bool
    source_type: str
    source_ref: str
    claim: Mapping[str, Any]
    confidence: float = 1.0
    issues: tuple[str, ...] = ()

    @property
    def severity(self) -> str:
        return GREEN if self.backed else RED


def _list_paper_ids(papers_dir: Path) -> set[str]:
    if not papers_dir.is_dir():
        return set()
    return {p.stem for p in papers_dir.glob("*.md") if p.is_file()}


def _resolve_spec_ref(spec_ref: str, search_dirs: Sequence[Path]) -> tuple[bool, str]:
    """Resolve a ``spec_file.md::section::lines`` style reference.

    Accepts plain filenames, ``file.md::section``, or ``file.md::section::L1-L2``.
    Returns ``(found, resolved_path)``.
    """
    if not spec_ref or spec_ref == "none":
        return False, ""
    parts = spec_ref.split("::")
    filename = parts[0].strip()
    if not filename:
        return False, ""
    if not filename.endswith(".md"):
        filename = filename + ".md"
    for directory in search_dirs:
        candidate = directory / filename
        if candidate.is_file():
            return True, str(candidate)
    return False, ""


def _resolve_tool_doc_ref(tool_doc_ref: str) -> tuple[bool, str]:
    """Resolve a ``API_name::version`` tool doc reference against vendor locks."""
    if not tool_doc_ref or tool_doc_ref == "none":
        return False, ""
    ref_lower = tool_doc_ref.strip().lower()
    parts = ref_lower.split("::")
    api_name = parts[0] if parts else ""
    version = parts[1] if len(parts) > 1 else ""

    if "portfolio.from_signals" in api_name or "from_signals" in api_name:
        return _check_vendor_lock(_VENDOR_VBT_LOCK, ("polakowo/vectorbt", "vectorbt[rust]"), version, "vectorbt")
    if "portfolio.from_orders" in api_name or "from_orders" in api_name:
        return _check_vendor_lock(_VENDOR_VBT_LOCK, ("polakowo/vectorbt", "vectorbt[rust]"), version, "vectorbt")
    if "portfolio.from_order_func" in api_name or "from_order_func" in api_name:
        return _check_vendor_lock(_VENDOR_VBT_LOCK, ("polakowo/vectorbt", "vectorbt[rust]"), version, "vectorbt")
    if "hftbacktest" in api_name:
        return _check_vendor_lock(_VENDOR_HBT_LOCK, ("nkaz001/hftbacktest",), version, "hftbacktest")
    return False, ""


def _check_vendor_lock(
    lock_path: Path,
    upstream_tokens: Sequence[str],
    version: str,
    package_hint: str,
) -> tuple[bool, str]:
    if not lock_path.is_file():
        return False, ""
    try:
        text = lock_path.read_text(encoding="utf-8").lower()
    except OSError:
        return False, ""
    if not all(token in text for token in upstream_tokens):
        return False, ""
    if version and version not in text:
        return False, ""
    return True, str(lock_path)


def trace_citation(
    *,
    paper_id: str | None = None,
    spec_ref: str | None = None,
    tool_doc_ref: str | None = None,
    papers_dir: Path | None = None,
    spec_dirs: Sequence[Path] | None = None,
) -> CitationResult:
    """Trace a citation claim to a real source.

    The claim may include any combination of ``paper_id`` (must match a
    ``library/papers/<paper_id>.md`` file), ``spec_ref`` (must resolve to a
    file in ``docs/project/`` or ``specs/``), and ``tool_doc_ref`` (must match
    a vendor lock). If none resolve, the result is ``UNBACKED``.
    """
    papers = papers_dir if papers_dir is not None else vault_papers_dir()
    spec_search = tuple(spec_dirs) if spec_dirs is not None else (_DOCS_PROJECT, _SPECS_ROOT)
    claim: dict[str, Any] = {
        "paper_id": paper_id,
        "spec_ref": spec_ref,
        "tool_doc_ref": tool_doc_ref,
    }
    issues: list[str] = []

    backed_sources: list[tuple[str, str]] = []

    if paper_id and paper_id != "none":
        ids = _list_paper_ids(papers)
        if paper_id in ids:
            backed_sources.append((SOURCE_PAPER, str(papers / f"{paper_id}.md")))
        else:
            issues.append(f"paper_id_not_found:{paper_id}")

    if spec_ref and spec_ref != "none":
        found, resolved = _resolve_spec_ref(spec_ref, spec_search)
        if found:
            backed_sources.append((SOURCE_SPEC, resolved))
        else:
            issues.append(f"spec_ref_not_found:{spec_ref}")

    if tool_doc_ref and tool_doc_ref != "none":
        found, resolved = _resolve_tool_doc_ref(tool_doc_ref)
        if found:
            backed_sources.append((SOURCE_TOOL_DOC, resolved))
        else:
            issues.append(f"tool_doc_ref_not_found:{tool_doc_ref}")

    if not any([paper_id, spec_ref, tool_doc_ref]):
        return CitationResult(
            backed=False,
            source_type=SOURCE_UNBACKED,
            source_ref="",
            claim=claim,
            confidence=0.0,
            issues=("no_citation_claim_provided",),
        )

    if backed_sources:
        source_type, source_ref = backed_sources[0]
        return CitationResult(
            backed=True,
            source_type=source_type,
            source_ref=source_ref,
            claim=claim,
            confidence=1.0,
            issues=tuple(issues),
        )
    return CitationResult(
        backed=False,
        source_type=SOURCE_UNBACKED,
        source_ref="",
        claim=claim,
        confidence=0.0,
        issues=tuple(issues) if issues else ("unbacked",),
    )


# ---------------------------------------------------------------------------
# 3. Invariant checker (B1-B8 from REVIEWER_CHARTER.md Pass B)
# ---------------------------------------------------------------------------

INVARIANT_IDS: Sequence[str] = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8")

INVARIANT_AUTHORITY: Mapping[str, str] = {
    "B1": "docs/REVIEWER_CHARTER.md#b1-filtration-integrity-f_t; chicago_cme_microstructure_mathematical_model.pdf",
    "B2": "docs/REVIEWER_CHARTER.md#b2-event-time-correctness; chicago_cme_microstructure_mathematical_model.pdf",
    "B3": "docs/REVIEWER_CHARTER.md#b3-no-lookahead--leakage; Ultimate_Quantitative_Finance_Researcher.pdf",
    "B4": "docs/REVIEWER_CHARTER.md#b4-walk-forward-discipline; BLUEPRINT.md",
    "B5": "docs/REVIEWER_CHARTER.md#b5-execution-realism; chicago_cme_microstructure_a_plus_developer_handoff.pdf",
    "B6": "docs/REVIEWER_CHARTER.md#b6-regime-pz_t--f_t; Ultimate_Quantitative_Finance_Researcher.pdf",
    "B7": "docs/REVIEWER_CHARTER.md#b7-trial-vs-production-data-lanes; rithmic_trial_hftbacktest_pipeline_prompt.pdf",
    "B8": "docs/REVIEWER_CHARTER.md#b8-production-failure-states; chicago_cme_a_plus_production_implementation_prompt.pdf",
}

INVARIANT_DESCRIPTIONS: Mapping[str, str] = {
    "B1": "Filtration F_t: only info available at or before decision time t",
    "B2": "Event-time: MBO marked events; no bar-as-causal smuggling",
    "B3": "No lookahead: no future labels, no random time splits",
    "B4": "Walk-forward: Discovery 2018-2020, Confirmation 2021-2022, Holdout 2023-2024, Recent 2025+",
    "B5": "Execution realism: latency bands, queue models, fees, net edge after costs",
    "B6": "Regime P(Z_t|F_t): no hardcoded regime strings without posterior",
    "B7": "Data lanes: no trial data in data/npz production paths",
    "B8": "Production failure states: stale halt, disconnect, clock drift, position mismatch, daily loss",
}

# Area table from REVIEWER_CHARTER.md (x means applies). "tests" area = scope-only.
AREA_INVARIANT_APPLICABILITY: Mapping[str, Mapping[str, bool]] = {
    "features_engine": {"B1": True, "B2": True, "B3": True, "B4": False, "B5": False, "B6": True, "B7": False, "B8": False},
    "backtest_pipeline": {"B1": True, "B2": True, "B3": True, "B4": True, "B5": True, "B6": False, "B7": False, "B8": False},
    "decision_engine": {"B1": True, "B2": False, "B3": True, "B4": True, "B5": True, "B6": True, "B7": False, "B8": True},
    "data_system": {"B1": True, "B2": True, "B3": True, "B4": False, "B5": False, "B6": False, "B7": True, "B8": False},
    "data": {"B1": False, "B2": True, "B3": True, "B4": False, "B5": False, "B6": False, "B7": True, "B8": False},
    "rithmic_trial": {"B1": True, "B2": True, "B3": False, "B4": False, "B5": True, "B6": False, "B7": True, "B8": False},
    "crypto_lane": {"B1": True, "B2": True, "B3": True, "B4": True, "B5": False, "B6": False, "B7": True, "B8": False},
    "equities_lane": {"B1": True, "B2": True, "B3": True, "B4": True, "B5": False, "B6": False, "B7": True, "B8": False},
    "workbench": {"B1": True, "B2": True, "B3": True, "B4": True, "B5": True, "B6": False, "B7": False, "B8": False},
    "options_lane": {"B1": True, "B2": True, "B3": True, "B4": True, "B5": False, "B6": False, "B7": True, "B8": False},
    "chi404": {"B1": False, "B2": False, "B3": False, "B4": False, "B5": True, "B6": False, "B7": False, "B8": True},
    "rithmic_gateway": {"B1": True, "B2": True, "B3": False, "B4": False, "B5": True, "B6": False, "B7": False, "B8": True},
    "infrastructure": {"B1": False, "B2": False, "B3": False, "B4": False, "B5": True, "B6": False, "B7": False, "B8": True},
    "tests": {bid: False for bid in INVARIANT_IDS},
}


@dataclass(frozen=True)
class InvariantCheck:
    invariant_id: str
    status: str  # pass | fail | na
    authority: str
    finding: str = ""

    @property
    def severity(self) -> str:
        if self.status == "fail":
            return RED
        if self.status == "na":
            return YELLOW
        return GREEN


@dataclass(frozen=True)
class InvariantResult:
    checks: tuple[InvariantCheck, ...]
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def any_red(self) -> bool:
        return any(c.severity == RED for c in self.checks)

    @property
    def red_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == RED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checks": [
                {
                    "invariant_id": c.invariant_id,
                    "status": c.status,
                    "authority": c.authority,
                    "severity": c.severity,
                    "finding": c.finding,
                }
                for c in self.checks
            ],
            "findings": list(self.findings),
            "any_red": self.any_red,
            "red_count": self.red_count,
        }


def _applicable_invariants(area: str) -> Mapping[str, bool]:
    area_key = area.strip().lower()
    if area_key in AREA_INVARIANT_APPLICABILITY:
        return AREA_INVARIANT_APPLICABILITY[area_key]
    # When unknown, the charter says "run the full B1-B8 checklist".
    return {bid: True for bid in INVARIANT_IDS}


def check_invariants(
    *,
    area: str,
    invariant_results: Mapping[str, str] | None = None,
    findings: Sequence[str] | None = None,
) -> InvariantResult:
    """Apply B1-B8 for a code area, citing authority for each check.

    ``invariant_results`` maps ``"B1"``→``"pass"|"fail"|"na"``. Missing keys
    default to ``"na"``. Each returned :class:`InvariantCheck` carries the
    authority citation from :data:`INVARIANT_AUTHORITY`.

    .. note::

       This function is **deterministic** — it does not inspect diff text or
       source code to detect invariant violations. The caller (typically the
       ``cavecrew-reviewer`` agent running Pass B) is responsible for analyzing
       the diff and feeding the results mapping. The gate applies rules to
       those results; it does not replace the reviewer's judgment. This
       coupling is intentional: the gate must be LLM-free and deterministic,
       so invariant detection stays in the reviewer while invariant
       enforcement (fail-closed, authority citation, area applicability)
       lives here.
    """
    results = dict(invariant_results or {})
    applicable = _applicable_invariants(area)
    checks: list[InvariantCheck] = []
    finding_lines: list[str] = list(findings or [])
    for bid in INVARIANT_IDS:
        status = str(results.get(bid, "na")).strip().lower()
        if status not in {"pass", "fail", "na"}:
            status = "na"
        if not applicable.get(bid, True) and status == "na":
            status = "na"
        authority = INVARIANT_AUTHORITY[bid]
        finding = ""
        if status == "fail":
            finding = f"{bid} {INVARIANT_DESCRIPTIONS[bid]} — violation"
            finding_lines.append(f"{bid}: {finding} (authority: {authority})")
        checks.append(InvariantCheck(invariant_id=bid, status=status, authority=authority, finding=finding))
    return InvariantResult(checks=tuple(checks), findings=tuple(finding_lines))


# ---------------------------------------------------------------------------
# 4. Tool usage checker
# ---------------------------------------------------------------------------

VECTORBT_API_METHODS: Mapping[str, tuple[str, ...]] = {
    "Portfolio.from_signals": ("close", "entries", "exits"),
    "Portfolio.from_orders": ("close", "size",),
    "Portfolio.from_order_func": ("close", "order_func",),
}

HFTBACKTEST_LOCK_VERSION = "2.4.2"


@dataclass(frozen=True)
class ToolUsageResult:
    api_correct: bool
    version_match: bool
    issues: tuple[str, ...] = ()
    api_name: str = ""
    version: str = ""

    @property
    def severity(self) -> str:
        if self.issues:
            return RED
        return GREEN

    @property
    def ok(self) -> bool:
        return self.api_correct and self.version_match and not self.issues


def _check_vectorbt_call(call_site: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    api = str(call_site.get("api_name") or call_site.get("api") or "")
    api_norm = api.strip()
    matched = None
    for canonical in VECTORBT_API_METHODS:
        if canonical.lower().endswith(api_norm.lower()) or api_norm.lower().endswith(canonical.split(".")[-1].lower()):
            matched = canonical
            break
    if matched is None:
        # Hand-rolled backtester masquerading as VectorBT
        if "vectorbt" in api_norm.lower() or "portfolio" in api_norm.lower():
            issues.append(f"unknown_vectorbt_api:{api_norm}")
        else:
            issues.append(f"not_official_vectorbt_api:{api_norm or 'missing'}")
        return issues

    required_args = VECTORBT_API_METHODS[matched]
    provided = call_site.get("args") or call_site.get("kwargs") or {}
    if isinstance(provided, Mapping):
        provided_keys = set(str(k) for k in provided.keys())
    else:
        provided_keys = set()
    for arg in required_args:
        if arg not in provided_keys:
            issues.append(f"{matched}:missing_required_arg:{arg}")
    engine = str(call_site.get("engine") or "").lower()
    scope = str(call_site.get("scope") or call_site.get("screening_scope") or "").lower().replace("-", "_")
    # Normalize common hyphenated forms to the canonical underscore form.
    _PAID_COMPUTE_SCOPES = {"paid", "paid_compute", "broad", "broad_screen", "all_model", "all_models", "refine"}
    if scope in _PAID_COMPUTE_SCOPES:
        if engine and engine != "rust":
            issues.append(f"{matched}:non_rust_engine_for_paid_compute_scope:{engine or 'missing'}")
    if call_site.get("hand_rolled") is True:
        issues.append(f"{matched}:hand_rolled_backtester_masquerade")
    return issues


def _check_hftbacktest_call(call_site: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    api = str(call_site.get("api_name") or call_site.get("api") or "")
    if "hftbacktest" not in api.lower():
        issues.append(f"not_hftbacktest_api:{api or 'missing'}")
        return issues
    upstream_ref = call_site.get("hftbacktest_upstream_ref") or call_site.get("source_lock_version")
    if not upstream_ref:
        issues.append("hftbacktest_missing_source_lock_evidence")
    elif str(upstream_ref) != HFTBACKTEST_LOCK_VERSION:
        issues.append(f"hftbacktest_version_mismatch:{upstream_ref}!={HFTBACKTEST_LOCK_VERSION}")
    has_cpp_evidence = call_site.get("cpp_hot_path_evidence") is True or call_site.get(
        "native_cpp_hot_path_evidence"
    ) is True
    if call_site.get("claims_production_realism") is True and not has_cpp_evidence:
        issues.append("hftbacktest_production_realism_without_cpp_hot_path_evidence")
    return issues


def check_tool_usage(call_site: Mapping[str, Any]) -> ToolUsageResult:
    """Verify a VectorBT or HftBacktest API call site matches official signatures.

    ``call_site`` keys:

    * ``tool``: ``"vectorbt"`` or ``"hftbacktest"``
    * ``api_name``: e.g. ``"Portfolio.from_signals"``
    * ``args`` / ``kwargs``: mapping of provided arguments
    * ``engine``: vectorbt engine string (``"rust"`` / ``"numba"``)
    * ``scope``: screening scope
    * ``hand_rolled``: True if a hand-rolled backtester masquerades as the tool
    * ``hftbacktest_upstream_ref`` / ``source_lock_version``: HBT version pin
    * ``claims_production_realism`` / ``cpp_hot_path_evidence``: HBT realism flags
    """
    tool = str(call_site.get("tool") or "").lower()
    issues: list[str] = []
    api_name = str(call_site.get("api_name") or call_site.get("api") or "")
    version = str(call_site.get("version") or "")

    if tool == "hftbacktest":
        issues = _check_hftbacktest_call(call_site)
        version_match = (
            str(call_site.get("hftbacktest_upstream_ref") or call_site.get("source_lock_version") or "")
            == HFTBACKTEST_LOCK_VERSION
        )
        api_correct = not any("not_hftbacktest" in i for i in issues)
    else:
        issues = _check_vectorbt_call(call_site)
        version_match = True
        if version and version != "1.0.0":
            version_match = False
            issues.append(f"vectorbt_version_mismatch:{version}!=1.0.0")
        api_correct = not any(i.startswith(("unknown_vectorbt", "not_official_vectorbt")) for i in issues)

    return ToolUsageResult(
        api_correct=api_correct,
        version_match=version_match,
        issues=tuple(issues),
        api_name=api_name,
        version=version,
    )


# ---------------------------------------------------------------------------
# 5. Artifact schema validator (delegates to existing validators)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactResult:
    valid: bool
    missing_fields: tuple[str, ...] = ()
    feature_plane_errors: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def severity(self) -> str:
        return GREEN if self.valid else RED


def validate_artifact_schema(
    artifact: Mapping[str, Any],
    *,
    artifact_type: str = "screening",
    run_screening_validator: bool = True,
) -> ArtifactResult:
    """Validate a screening/feature-plane artifact schema.

    Delegates to :func:`validate_screening_artifact` and
    :func:`feature_plane_validation_errors` from the existing modules — this
    gate does not duplicate schema logic.
    """
    missing: list[str] = []
    for field_name in SCREENING_ARTIFACT_REQUIRED_FIELDS:
        if field_name not in artifact:
            missing.append(field_name)
        elif artifact[field_name] == "":
            missing.append(field_name)
        elif artifact[field_name] is None and not field_name.endswith("_or_null"):
            missing.append(field_name)

    fp_errors: list[str] = list(feature_plane_validation_errors(artifact))

    issues: list[str] = list(missing)
    issues.extend(fp_errors)

    screening_ok = True
    if run_screening_validator and artifact_type == "screening" and not missing:
        try:
            validate_screening_artifact(artifact)
        except Exception as exc:  # the existing validator raises ValueError subclasses
            screening_ok = False
            issues.append(f"screening_artifact_validator_rejected:{exc}")
        else:
            screening_ok = True
    elif run_screening_validator and artifact_type == "screening" and missing:
        screening_ok = False

    valid = (not missing) and (not fp_errors) and screening_ok
    return ArtifactResult(
        valid=valid,
        missing_fields=tuple(missing),
        feature_plane_errors=tuple(fp_errors),
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------
# 6. Drift guard (7 patterns from 2026-06-17 decision)
# ---------------------------------------------------------------------------

DRIFT_PATTERN_FEATURES_AS_CLUES = "features_called_clues"
DRIFT_PATTERN_PER_EVENT_AS_UPLIFT = "per_event_profitability_as_context_uplift"
DRIFT_PATTERN_LAKE_AS_USAGE = "lake_existence_as_feature_usage"
DRIFT_PATTERN_BLOCK_ALL_FOR_ONE = "block_all_models_for_one_missing_family"
DRIFT_PATTERN_NON_RUST_AS_PAID = "non_rust_vbt_as_paid_compute_evidence"
DRIFT_PATTERN_VBT_AS_HFT_REALISM = "vbt_screening_as_hft_execution_realism"
DRIFT_PATTERN_HBT_NO_CPP = "hftbacktest_without_cpp_hot_path_as_production_realism"
DRIFT_PATTERN_PARALLEL_AUTHORITY = "creates_parallel_authority_docs"

DRIFT_PATTERNS: tuple[str, ...] = (
    DRIFT_PATTERN_FEATURES_AS_CLUES,
    DRIFT_PATTERN_PER_EVENT_AS_UPLIFT,
    DRIFT_PATTERN_LAKE_AS_USAGE,
    DRIFT_PATTERN_BLOCK_ALL_FOR_ONE,
    DRIFT_PATTERN_NON_RUST_AS_PAID,
    DRIFT_PATTERN_VBT_AS_HFT_REALISM,
    DRIFT_PATTERN_HBT_NO_CPP,
    DRIFT_PATTERN_PARALLEL_AUTHORITY,
)

_DRIFT_LABELS: Mapping[str, str] = {
    DRIFT_PATTERN_FEATURES_AS_CLUES: "features called 'clues' in implementation artifacts",
    DRIFT_PATTERN_PER_EVENT_AS_UPLIFT: "per-event standalone profitability treated as context uplift",
    DRIFT_PATTERN_LAKE_AS_USAGE: "feature usage claimed from lake existence only",
    DRIFT_PATTERN_BLOCK_ALL_FOR_ONE: "all models blocked because one optional data family is missing",
    DRIFT_PATTERN_NON_RUST_AS_PAID: "non-Rust VectorBT treated as broad paid-compute evidence",
    DRIFT_PATTERN_VBT_AS_HFT_REALISM: "VectorBT screening treated as HFT execution realism",
    DRIFT_PATTERN_HBT_NO_CPP: "HftBacktest without C++ hot-path evidence treated as production realism",
    DRIFT_PATTERN_PARALLEL_AUTHORITY: "creates parallel authority docs instead of updating canonical files",
}


@dataclass(frozen=True)
class DriftResult:
    detected_patterns: tuple[str, ...]
    findings: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.detected_patterns

    @property
    def severity(self) -> str:
        return GREEN if self.clean else RED


def _detect_drift_patterns(text: str) -> list[str]:
    """Detect the 7 drift patterns from prose/artifact text.

    Deterministic keyword/phrase rules — no LLM judgment. These intentionally
    err on the side of flagging: the gate is fail-closed.
    """
    lower = (text or "").lower()
    detected: list[str] = []

    # 1. features called "clues"
    if re.search(r"\bclues?\b", lower) and re.search(r"feature|signal|model", lower):
        # Avoid false positive on the spec/charter text itself mentioning
        # "clues" only when paired with "feature" in implementation prose.
        if re.search(r"feature[^.\n]{0,40}\bclues?\b", lower) or re.search(
            r"\bclues?\b[^.\n]{0,40}feature", lower
        ):
            detected.append(DRIFT_PATTERN_FEATURES_AS_CLUES)

    # 2. per-event profitability as context uplift
    if "per-event" in lower and "context uplift" in lower:
        detected.append(DRIFT_PATTERN_PER_EVENT_AS_UPLIFT)
    elif "per_event" in lower and "context_uplift" in lower:
        detected.append(DRIFT_PATTERN_PER_EVENT_AS_UPLIFT)
    elif "standalone profitability" in lower and "context" in lower:
        detected.append(DRIFT_PATTERN_PER_EVENT_AS_UPLIFT)

    # 3. lake existence as feature usage
    if "lake" in lower and ("feature usage" in lower or "feature_consumption" in lower):
        if "lake existence" in lower or "lake exists" in lower or "lake_present" in lower:
            detected.append(DRIFT_PATTERN_LAKE_AS_USAGE)
    if re.search(r"lake[^.\n]{0,30}(implies|means|proves)[^.\n]{0,30}(feature|consumption)", lower):
        detected.append(DRIFT_PATTERN_LAKE_AS_USAGE)

    # 4. block all models for one missing family
    if "block all" in lower and "missing" in lower and ("family" in lower or "model" in lower):
        detected.append(DRIFT_PATTERN_BLOCK_ALL_FOR_ONE)
    elif "all models" in lower and "one optional" in lower and "missing" in lower:
        detected.append(DRIFT_PATTERN_BLOCK_ALL_FOR_ONE)

    # 5. non-Rust VBT as paid-compute evidence
    if "non-rust" in lower and ("paid" in lower or "broad" in lower):
        detected.append(DRIFT_PATTERN_NON_RUST_AS_PAID)
    if "vectorbt_engine" in lower and "numba" in lower and ("paid" in lower or "broad" in lower):
        detected.append(DRIFT_PATTERN_NON_RUST_AS_PAID)
    if "engine" in lower and "numba" in lower and "paid_compute" in lower:
        detected.append(DRIFT_PATTERN_NON_RUST_AS_PAID)

    # 6. VBT screening as HFT execution realism
    if "vectorbt" in lower and "screening" in lower and "hft" in lower and "realism" in lower:
        detected.append(DRIFT_PATTERN_VBT_AS_HFT_REALISM)
    if "vectorbt" in lower and "screen" in lower and "execution realism" in lower:
        detected.append(DRIFT_PATTERN_VBT_AS_HFT_REALISM)

    # 7. HftBacktest without C++ hot-path as production realism
    if "hftbacktest" in lower and "production realism" in lower:
        if "c++" not in lower and "cpp" not in lower and "hot_path" not in lower and "hot-path" not in lower:
            detected.append(DRIFT_PATTERN_HBT_NO_CPP)
    if "hftbacktest" in lower and "production" in lower and "realism" in lower:
        if "cpp_hot_path_evidence" not in lower and "native_cpp" not in lower and "c++ hot-path" not in lower:
            detected.append(DRIFT_PATTERN_HBT_NO_CPP)

    # 8. creates parallel authority docs
    if "new source-of-truth" in lower or "parallel authority" in lower:
        detected.append(DRIFT_PATTERN_PARALLEL_AUTHORITY)
    if "create" in lower and "source of truth" in lower:
        detected.append(DRIFT_PATTERN_PARALLEL_AUTHORITY)
    if "another plan" in lower and "instead of" in lower and "canonical" in lower:
        detected.append(DRIFT_PATTERN_PARALLEL_AUTHORITY)

    # de-dup, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for p in detected:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def check_drift(
    *,
    text: str | None = None,
    artifact: Mapping[str, Any] | None = None,
    patterns: Iterable[str] | None = None,
) -> DriftResult:
    """Check for the 7 drift patterns from the 2026-06-17 decision.

    Either ``text`` (prose/diff/PR description) or ``artifact`` (a mapping with
    drift-flag fields) may be supplied. Explicit ``patterns`` short-circuits
    detection and is useful for tests and direct flagging.
    """
    if patterns is not None:
        detected = [p for p in patterns if p in DRIFT_PATTERNS]
        findings = [f"drift_pattern_detected:{p} — {_DRIFT_LABELS[p]}" for p in detected]
        return DriftResult(detected_patterns=tuple(detected), findings=tuple(findings))

    detected: list[str] = []
    if text:
        detected.extend(_detect_drift_patterns(text))
    if artifact is not None:
        artifact_text = " ".join(f"{k}:{v}" for k, v in artifact.items())
        detected.extend(_detect_drift_patterns(artifact_text))
        # Structured flags
        if artifact.get("features_called_clues") is True:
            detected.append(DRIFT_PATTERN_FEATURES_AS_CLUES)
        if artifact.get("per_event_profitability_as_context_uplift") is True:
            detected.append(DRIFT_PATTERN_PER_EVENT_AS_UPLIFT)
        if artifact.get("lake_existence_as_feature_usage") is True:
            detected.append(DRIFT_PATTERN_LAKE_AS_USAGE)
        if artifact.get("block_all_models_for_one_missing_family") is True:
            detected.append(DRIFT_PATTERN_BLOCK_ALL_FOR_ONE)
        if artifact.get("non_rust_vbt_as_paid_compute_evidence") is True:
            detected.append(DRIFT_PATTERN_NON_RUST_AS_PAID)
        if artifact.get("vbt_screening_as_hft_execution_realism") is True:
            detected.append(DRIFT_PATTERN_VBT_AS_HFT_REALISM)
        if artifact.get("hftbacktest_without_cpp_hot_path_as_production_realism") is True:
            detected.append(DRIFT_PATTERN_HBT_NO_CPP)
        if artifact.get("creates_parallel_authority_docs") is True:
            detected.append(DRIFT_PATTERN_PARALLEL_AUTHORITY)

    # de-dup preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for p in detected:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    findings = [f"drift_pattern_detected:{p} — {_DRIFT_LABELS[p]}" for p in ordered]
    return DriftResult(detected_patterns=tuple(ordered), findings=tuple(findings))


# ---------------------------------------------------------------------------
# 7. Scope honesty checker
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeHonestyResult:
    honest: bool
    issues: tuple[str, ...] = ()

    @property
    def severity(self) -> str:
        return GREEN if self.honest else RED


def check_scope_honesty(
    *,
    subset_pytest_claimed_as_scope_green: bool = False,
    waived_verify_claimed_as_done: bool = False,
    plan_todo_theater: bool = False,
    scope_green_without_exit_code: bool = False,
    missing_verify_tail: bool = False,
) -> ScopeHonestyResult:
    """Enforce scope honesty (spec GATE_RULES §8).

    - Subset pytest ≠ scope-green
    - User-waived verify ≠ done
    - Plan todo theater forbidden (status:completed requires pasted green output)
    """
    issues: list[str] = []
    if subset_pytest_claimed_as_scope_green:
        issues.append("subset_pytest_claimed_as_scope_green")
    if waived_verify_claimed_as_done:
        issues.append("waived_verify_claimed_as_done")
    if plan_todo_theater:
        issues.append("plan_todo_theater_forbidden")
    if scope_green_without_exit_code:
        issues.append("scope_green_requires_full_scope_pytest_with_exit_code_and_output_tail")
    if missing_verify_tail:
        issues.append("verify_run_missing_exit_code_or_output_tail")
    return ScopeHonestyResult(honest=not issues, issues=tuple(issues))


# ---------------------------------------------------------------------------
# 8. Gate decision
# ---------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"


@dataclass(frozen=True)
class GateVerdict:
    verdict: str
    reasons: tuple[str, ...]
    red_count: int
    yellow_count: int
    fable_checklist: FableChecklist | None = None
    citation_results: tuple[CitationResult, ...] = field(default_factory=tuple)
    invariant_result: InvariantResult | None = None
    tool_usage_results: tuple[ToolUsageResult, ...] = field(default_factory=tuple)
    artifact_result: ArtifactResult | None = None
    drift_result: DriftResult | None = None
    scope_honesty_result: ScopeHonestyResult | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "red_count": self.red_count,
            "yellow_count": self.yellow_count,
            "fable_checklist": self.fable_checklist.as_dict() if self.fable_checklist else None,
            "citations": [
                {
                    "backed": c.backed,
                    "source_type": c.source_type,
                    "source_ref": c.source_ref,
                    "severity": c.severity,
                    "issues": list(c.issues),
                }
                for c in self.citation_results
            ],
            "invariants": self.invariant_result.as_dict() if self.invariant_result else None,
            "tool_usage": [
                {
                    "api_correct": t.api_correct,
                    "version_match": t.version_match,
                    "issues": list(t.issues),
                    "severity": t.severity,
                }
                for t in self.tool_usage_results
            ],
            "artifact": {
                "valid": self.artifact_result.valid if self.artifact_result else None,
                "issues": list(self.artifact_result.issues) if self.artifact_result else [],
                "severity": self.artifact_result.severity if self.artifact_result else None,
            }
            if self.artifact_result
            else None,
            "drift": {
                "clean": self.drift_result.clean if self.drift_result else None,
                "detected_patterns": list(self.drift_result.detected_patterns) if self.drift_result else [],
                "severity": self.drift_result.severity if self.drift_result else None,
            }
            if self.drift_result
            else None,
            "scope_honesty": {
                "honest": self.scope_honesty_result.honest if self.scope_honesty_result else None,
                "issues": list(self.scope_honesty_result.issues) if self.scope_honesty_result else [],
                "severity": self.scope_honesty_result.severity if self.scope_honesty_result else None,
            }
            if self.scope_honesty_result
            else None,
        }


def gate_decision(
    *,
    fable_checklist: FableChecklist | None = None,
    citation_results: Sequence[CitationResult] | None = None,
    invariant_result: InvariantResult | None = None,
    tool_usage_results: Sequence[ToolUsageResult] | None = None,
    artifact_result: ArtifactResult | None = None,
    drift_result: DriftResult | None = None,
    scope_honesty_result: ScopeHonestyResult | None = None,
) -> GateVerdict:
    """Aggregate all results and emit PASS or REJECT with reasons.

    Any red finding → REJECT. The Fable checklist is a hard prerequisite: if
    provided and not all-true, the gate rejects with a Fable-entry failure.
    """
    reasons: list[str] = []
    red_count = 0
    yellow_count = 0

    if fable_checklist is not None and not fable_checklist.all_true:
        red_count += 1
        failed = [name for name, ok in fable_checklist.as_dict().items() if not ok]
        reasons.append(f"fable_entry_checklist_failed:{','.join(failed)}")

    for idx, citation in enumerate(citation_results or []):
        if citation.severity == RED:
            red_count += 1
            reasons.append(f"citation[{idx}]_unbacked:{';'.join(citation.issues) or 'unbacked'}")

    if invariant_result is not None:
        for check in invariant_result.checks:
            if check.severity == RED:
                red_count += 1
                reasons.append(f"invariant_{check.invariant_id}_fail:{check.finding or 'violation'}")
            elif check.severity == YELLOW:
                yellow_count += 1

    for idx, tool in enumerate(tool_usage_results or []):
        if tool.severity == RED:
            red_count += 1
            reasons.append(f"tool_usage[{idx}]_incorrect:{';'.join(tool.issues) or 'incorrect'}")

    if artifact_result is not None and artifact_result.severity == RED:
        red_count += 1
        reasons.append(f"artifact_schema_invalid:{';'.join(artifact_result.issues) or 'invalid'}")

    if drift_result is not None and drift_result.severity == RED:
        red_count += 1
        for pattern in drift_result.detected_patterns:
            reasons.append(f"drift_pattern:{pattern}")

    if scope_honesty_result is not None and scope_honesty_result.severity == RED:
        red_count += 1
        for issue in scope_honesty_result.issues:
            reasons.append(f"scope_honesty:{issue}")

    verdict = VERDICT_PASS if red_count == 0 else VERDICT_REJECT
    return GateVerdict(
        verdict=verdict,
        reasons=tuple(reasons),
        red_count=red_count,
        yellow_count=yellow_count,
        fable_checklist=fable_checklist,
        citation_results=tuple(citation_results or []),
        invariant_result=invariant_result,
        tool_usage_results=tuple(tool_usage_results or []),
        artifact_result=artifact_result,
        drift_result=drift_result,
        scope_honesty_result=scope_honesty_result,
    )


def run_gate(
    *,
    fable_checklist: FableChecklist,
    citations: Sequence[Mapping[str, Any]] | None = None,
    area: str = "backtest_pipeline",
    invariant_results: Mapping[str, str] | None = None,
    invariant_findings: Sequence[str] | None = None,
    call_sites: Sequence[Mapping[str, Any]] | None = None,
    artifact: Mapping[str, Any] | None = None,
    artifact_type: str = "screening",
    drift_text: str | None = None,
    drift_artifact: Mapping[str, Any] | None = None,
    drift_patterns: Iterable[str] | None = None,
    subset_pytest_claimed_as_scope_green: bool = False,
    waived_verify_claimed_as_done: bool = False,
    plan_todo_theater: bool = False,
    scope_green_without_exit_code: bool = False,
    missing_verify_tail: bool = False,
    papers_dir: Path | None = None,
    spec_dirs: Sequence[Path] | None = None,
) -> GateVerdict:
    """Run the full gate pipeline and return the aggregate verdict.

    Convenience entrypoint that wires the individual checkers into
    :func:`gate_decision`. Each input is optional; omitted sections do not
    contribute findings.
    """
    citation_results: list[CitationResult] = []
    for claim in citations or []:
        citation_results.append(
            trace_citation(
                paper_id=claim.get("paper_id"),
                spec_ref=claim.get("spec_ref"),
                tool_doc_ref=claim.get("tool_doc_ref"),
                papers_dir=papers_dir,
                spec_dirs=spec_dirs,
            )
        )

    invariant_result = check_invariants(area=area, invariant_results=invariant_results, findings=invariant_findings)

    tool_results = [check_tool_usage(call_site) for call_site in (call_sites or [])]

    artifact_result = None
    if artifact is not None:
        artifact_result = validate_artifact_schema(artifact, artifact_type=artifact_type)

    drift_result = check_drift(text=drift_text, artifact=drift_artifact, patterns=drift_patterns)

    scope_honesty_result = check_scope_honesty(
        subset_pytest_claimed_as_scope_green=subset_pytest_claimed_as_scope_green,
        waived_verify_claimed_as_done=waived_verify_claimed_as_done,
        plan_todo_theater=plan_todo_theater,
        scope_green_without_exit_code=scope_green_without_exit_code,
        missing_verify_tail=missing_verify_tail,
    )

    return gate_decision(
        fable_checklist=fable_checklist,
        citation_results=citation_results,
        invariant_result=invariant_result,
        tool_usage_results=tool_results,
        artifact_result=artifact_result,
        drift_result=drift_result,
        scope_honesty_result=scope_honesty_result,
    )


__all__ = [
    "FABLE_CHECKBOXES",
    "FableChecklist",
    "validate_fable_entry_checklist",
    "CitationResult",
    "trace_citation",
    "SOURCE_PAPER",
    "SOURCE_SPEC",
    "SOURCE_TOOL_DOC",
    "SOURCE_UNBACKED",
    "InvariantCheck",
    "InvariantResult",
    "INVARIANT_IDS",
    "INVARIANT_AUTHORITY",
    "INVARIANT_DESCRIPTIONS",
    "AREA_INVARIANT_APPLICABILITY",
    "check_invariants",
    "ToolUsageResult",
    "VECTORBT_API_METHODS",
    "HFTBACKTEST_LOCK_VERSION",
    "check_tool_usage",
    "ArtifactResult",
    "validate_artifact_schema",
    "DriftResult",
    "DRIFT_PATTERNS",
    "DRIFT_PATTERN_FEATURES_AS_CLUES",
    "DRIFT_PATTERN_PER_EVENT_AS_UPLIFT",
    "DRIFT_PATTERN_LAKE_AS_USAGE",
    "DRIFT_PATTERN_BLOCK_ALL_FOR_ONE",
    "DRIFT_PATTERN_NON_RUST_AS_PAID",
    "DRIFT_PATTERN_VBT_AS_HFT_REALISM",
    "DRIFT_PATTERN_HBT_NO_CPP",
    "DRIFT_PATTERN_PARALLEL_AUTHORITY",
    "check_drift",
    "ScopeHonestyResult",
    "check_scope_honesty",
    "GateVerdict",
    "VERDICT_PASS",
    "VERDICT_REJECT",
    "gate_decision",
    "run_gate",
    "vault_papers_dir",
    "repo_docs_project_dir",
    "repo_specs_dir",
    "repo_docs_dir",
]