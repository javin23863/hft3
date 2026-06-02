"""Write the 14-file research intake bundle (Phase 3).

The bundle is a single directory under `research_inputs/{research_id}/`
containing:

  1.  source_document_path                (text, one path)
  2.  extracted_text.md                   (markdown)
  3.  extracted_equations.json            (List[Equation])
  4.  extracted_tables.json               (List[Table])
  5.  thesis_summary.json                 (ThesisSummary)
  6.  assumptions.json                    (List[Assumption])
  7.  required_data.json                  (List[DataRequirement])
  8.  required_features.json              (List[FeatureRequirement])
  9.  proposed_signal_logic.json          (SignalLogic)
  10. proposed_execution_logic.json       (ExecutionLogic)
  11. parameter_ranges.json               (List[ParameterRange])
  12. failure_modes.json                  (List[FailureMode])
  13. testable_hypotheses.json            (List[TestableHypothesis])
  14. experiment_translation_notes.json   (ExperimentTranslationNotes)

The writer never silently drops data. Invalid bundles are still written so
humans can inspect the raw LLM output, with `quarantine=True` set.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from research_pipeline.intake_schema import (
    Assumption,
    DataRequirement,
    Equation,
    ExecutionLogic,
    ExperimentTranslationNotes,
    FailureMode,
    FeatureRequirement,
    ParameterRange,
    SignalLogic,
    Table,
    TestableHypothesis,
    ThesisSummary,
    detect_intake_quarantine,
)


BUNDLE_FILES: tuple[str, ...] = (
    "source_document_path",
    "extracted_text.md",
    "extracted_equations.json",
    "extracted_tables.json",
    "thesis_summary.json",
    "assumptions.json",
    "required_data.json",
    "required_features.json",
    "proposed_signal_logic.json",
    "proposed_execution_logic.json",
    "parameter_ranges.json",
    "failure_modes.json",
    "testable_hypotheses.json",
    "experiment_translation_notes.json",
)


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_intake_bundle(
    research_id: str,
    source_path: Path,
    intake_dir: Path,
    *,
    extracted_text: str,
    thesis_summary: ThesisSummary,
    assumptions: List[Assumption],
    required_data: List[DataRequirement],
    required_features: List[FeatureRequirement],
    signal_logic: SignalLogic,
    execution_logic: ExecutionLogic,
    parameter_ranges: List[ParameterRange],
    failure_modes: List[FailureMode],
    testable_hypotheses: List[TestableHypothesis],
    translation_notes: ExperimentTranslationNotes,
    equations: Optional[List[Equation]] = None,
    tables: Optional[List[Table]] = None,
) -> Path:
    """Write the 14 files into `intake_dir / research_id`. Returns the bundle dir."""
    bundle = intake_dir / research_id
    bundle.mkdir(parents=True, exist_ok=True)

    reasons = detect_intake_quarantine(
        thesis=thesis_summary,
        signal=signal_logic,
        parameters=parameter_ranges,
        hypotheses=testable_hypotheses,
        notes=translation_notes,
    )
    if reasons and not translation_notes.quarantine:
        translation_notes.quarantine = True
        translation_notes.quarantine_reasons = sorted(
            set(translation_notes.quarantine_reasons) | set(reasons)
        )

    (bundle / "source_document_path").write_text(
        str(source_path.resolve()), encoding="utf-8"
    )
    (bundle / "extracted_text.md").write_text(extracted_text, encoding="utf-8")
    _dump_json(bundle / "extracted_equations.json", [e.model_dump() for e in (equations or [])])
    _dump_json(bundle / "extracted_tables.json", [t.model_dump() for t in (tables or [])])
    _dump_json(bundle / "thesis_summary.json", thesis_summary.model_dump())
    _dump_json(bundle / "assumptions.json", [a.model_dump() for a in assumptions])
    _dump_json(bundle / "required_data.json", [d.model_dump() for d in required_data])
    _dump_json(bundle / "required_features.json", [f.model_dump() for f in required_features])
    _dump_json(bundle / "proposed_signal_logic.json", signal_logic.model_dump())
    _dump_json(bundle / "proposed_execution_logic.json", execution_logic.model_dump())
    _dump_json(bundle / "parameter_ranges.json", [p.model_dump() for p in parameter_ranges])
    _dump_json(bundle / "failure_modes.json", [m.model_dump() for m in failure_modes])
    _dump_json(bundle / "testable_hypotheses.json", [h.model_dump() for h in testable_hypotheses])
    _dump_json(bundle / "experiment_translation_notes.json", translation_notes.model_dump())
    return bundle


def is_quarantined(bundle_dir: Path) -> bool:
    """Read `experiment_translation_notes.json` and return its `quarantine` flag."""
    notes_path = bundle_dir / "experiment_translation_notes.json"
    if not notes_path.is_file():
        return False
    try:
        data = json.loads(notes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("quarantine"))


def load_intake_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Read back the 14 files into a dict. Useful for tests and CLI."""
    result: dict[str, Any] = {}
    for name in BUNDLE_FILES:
        path = bundle_dir / name
        if not path.is_file():
            result[name] = None if not name.endswith(".md") and name != "source_document_path" else None
            continue
        if name == "source_document_path":
            result[name] = path.read_text(encoding="utf-8").strip()
        elif name == "extracted_text.md":
            result[name] = path.read_text(encoding="utf-8")
        else:
            try:
                result[name] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                result[name] = {"_error": f"json decode failed: {exc}"}
    return result
