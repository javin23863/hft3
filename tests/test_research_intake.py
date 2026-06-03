"""Phase 3 tests for the HFT3 research intake bundle.

Covers the 14-file writer, pydantic schema validation, quarantine
detection, and the LLM-cannot-promote-model boundary.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research_pipeline.intake_bundle import (
    BUNDLE_FILES,
    is_quarantined,
    load_intake_bundle,
    write_intake_bundle,
)
from research_pipeline.intake_schema import (
    Assumption,
    DataRequirement,
    ExecutionLogic,
    ExperimentTranslationNotes,
    FailureMode,
    FeatureRequirement,
    ParameterRange,
    SignalLogic,
    TestableHypothesis,
    ThesisSummary,
    detect_intake_quarantine,
)


def _full_payload():
    return {
        "thesis_summary": ThesisSummary(
            main_thesis="Mean reversion on NQ after 9:30 stop runs exploits post-event liquidity voids.",
            instrument_scope=["NQ"],
            time_horizon="intraday_5min",
            confidence=0.7,
        ),
        "assumptions": [
            Assumption(
                assumption_id="a1",
                statement="Stop runs at 9:30 reflect temporary liquidity voids",
                category="market_structure",
                criticality="high",
            )
        ],
        "required_data": [
            DataRequirement(
                data_id="d1",
                name="NQ MBO L3",
                source="cme_depth",
                granularity="tick",
                history_years=2.0,
                vendor="databento",
            )
        ],
        "required_features": [
            FeatureRequirement(
                feature_id="f1",
                name="book_imbalance_top5",
                definition="(bid_size - ask_size) / (bid_size + ask_size) at top 5 levels",
                feature_engine_slug="book_imbalance_top5",
                group="microstructure",
            )
        ],
        "signal_logic": SignalLogic(
            signal="book_imbalance_top5 < -0.6",
            entry=["enter_long when signal flips positive and spread < 2 ticks"],
            exit=["exit when book_imbalance_top5 > 0.2 or 30 bars elapsed"],
            time_stop_bars=30,
            regime_filter="exclude FOMC days",
        ),
        "execution_logic": ExecutionLogic(
            order_types=["limit", "post_only"],
            latency_budget_us=80,
            cost_model="per_share",
            slippage_bps=0.5,
            max_position=4,
            venue="CME",
        ),
        "parameter_ranges": [
            ParameterRange(param="signal_threshold", min=-0.9, max=-0.3, default=-0.6, distribution="uniform"),
            ParameterRange(param="time_stop_bars", min=10, max=60, default=30, distribution="uniform"),
        ],
        "failure_modes": [
            FailureMode(
                mode_id="fm1",
                condition="FOMC day",
                severity="high",
                mitigation="regime_filter excludes FOMC",
                detection_signal="event_window == FOMC",
            )
        ],
        "testable_hypotheses": [
            TestableHypothesis(
                hypothesis_id="h1",
                statement="Mean reversion signal yields positive Sharpe on non-FOMC NQ intraday.",
                pass_criteria="Sharpe > 0.5 over 2 years OOS",
                fail_criteria="Sharpe < 0 over 2 years OOS",
                metric="sharpe",
                threshold=0.5,
                evaluation_window="2023-2024 OOS",
            )
        ],
        "translation_notes": ExperimentTranslationNotes(
            missing_info=[],
            hft3_implementation_reqs=[
                "use databento NQ MBO L3",
                "use latency_summary.json to bound slippage",
            ],
            confidence=0.7,
        ),
    }


def test_intake_bundle_writes_all_14_files(tmp_path: Path) -> None:
    payload = _full_payload()
    src = tmp_path / "input.md"
    src.write_text("paper text here", encoding="utf-8")
    bundle = write_intake_bundle(
        research_id="r1",
        source_path=src,
        intake_dir=tmp_path / "intake",
        extracted_text="paper text here",
        **payload,
    )
    for name in BUNDLE_FILES:
        path = bundle / name
        assert path.is_file(), f"missing file: {name}"
    assert {path.name for path in bundle.iterdir()} == set(BUNDLE_FILES)
    assert not is_quarantined(bundle)


def test_source_document_path_is_absolute(tmp_path: Path) -> None:
    src = tmp_path / "input.md"
    src.write_text("body", encoding="utf-8")
    payload = _full_payload()
    bundle = write_intake_bundle(
        research_id="r1",
        source_path=src,
        intake_dir=tmp_path / "intake",
        extracted_text="body",
        **payload,
    )
    content = (bundle / "source_document_path").read_text(encoding="utf-8").strip()
    assert Path(content).is_absolute()
    with pytest.raises(ValueError):
        write_intake_bundle(
            research_id="../escape",
            source_path=src,
            intake_dir=tmp_path / "intake",
            extracted_text="body",
            **payload,
        )
    with pytest.raises(ValueError):
        write_intake_bundle(
            research_id=".",
            source_path=src,
            intake_dir=tmp_path / "intake",
            extracted_text="body",
            **payload,
        )


def test_intake_bundle_invalid_thesis_is_quarantined(tmp_path: Path) -> None:
    """A vague input should produce a bundle that is marked quarantined."""
    src = tmp_path / "vague.txt"
    src.write_text("vague", encoding="utf-8")
    payload = _full_payload()
    payload["thesis_summary"] = ThesisSummary(
        main_thesis="vague",
        instrument_scope=[],
        time_horizon="",
    )
    payload["parameter_ranges"] = []
    payload["testable_hypotheses"] = []
    payload["translation_notes"].quarantine = True
    payload["translation_notes"].quarantine_reasons = ["existing_llm_quarantine"]
    bundle = write_intake_bundle(
        research_id="vague1",
        source_path=src,
        intake_dir=tmp_path / "intake",
        extracted_text="vague",
        **payload,
    )
    assert is_quarantined(bundle)
    notes = json.loads((bundle / "experiment_translation_notes.json").read_text(encoding="utf-8"))
    assert notes["quarantine"] is True
    assert notes["quarantine_reasons"], "expected at least one quarantine reason"
    assert "existing_llm_quarantine" in notes["quarantine_reasons"]
    assert any("thesis" in r for r in notes["quarantine_reasons"])
    (bundle / "experiment_translation_notes.json").unlink()
    assert is_quarantined(bundle)
    (bundle / "experiment_translation_notes.json").write_text("{not-json", encoding="utf-8")
    assert is_quarantined(bundle)


def test_param_range_min_eq_max_is_quarantined(tmp_path: Path) -> None:
    src = tmp_path / "ok.md"
    src.write_text("ok", encoding="utf-8")
    payload = _full_payload()
    payload["parameter_ranges"] = [
        ParameterRange(param="bad", min=0.5, max=0.5, default=0.5)
    ]
    bundle = write_intake_bundle(
        research_id="bad-param",
        source_path=src,
        intake_dir=tmp_path / "intake",
        extracted_text="ok",
        **payload,
    )
    assert is_quarantined(bundle)


def test_detect_intake_quarantine_reasons() -> None:
    thesis = ThesisSummary(main_thesis="x" * 5, instrument_scope=[])
    sig = SignalLogic(signal="", entry=[], exit=[])
    params: list[ParameterRange] = []
    hyps: list[TestableHypothesis] = []
    notes = ExperimentTranslationNotes(
        missing_info=["a"], hft3_implementation_reqs=[]
    )
    reasons = detect_intake_quarantine(thesis, sig, params, hyps, notes)
    assert "thesis_under_30_chars" in reasons
    assert "no_instrument_scope" in reasons
    assert "empty_signal_logic" in reasons
    assert "no_parameter_ranges" in reasons
    assert "no_testable_hypotheses" in reasons


def test_load_intake_bundle_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "input.md"
    src.write_text("body", encoding="utf-8")
    payload = _full_payload()
    bundle = write_intake_bundle(
        research_id="r1",
        source_path=src,
        intake_dir=tmp_path / "intake",
        extracted_text="body",
        **payload,
    )
    loaded = load_intake_bundle(bundle)
    assert loaded["source_document_path"]
    assert loaded["extracted_text.md"] == "body"
    assert loaded["thesis_summary.json"]["main_thesis"].startswith("Mean reversion")
    assert loaded["testable_hypotheses.json"][0]["hypothesis_id"] == "h1"
    assert loaded["experiment_translation_notes.json"]["quarantine"] is False


def test_llm_cannot_promote_model_static_check() -> None:
    """Static AST check: the LLM-facing module must not import any
    promotion / registry module."""
    forbidden = {"promote", "save_registry", "load_registry", "append_record", "evaluate_promotion_gate"}
    roots = [
        Path("packages/research_pipeline/document_ingestion.py"),
        Path("packages/research_pipeline/llm.py"),
        Path("packages/research_pipeline/intake_bundle.py"),
        Path("packages/research_pipeline/intake_schema.py"),
    ]
    for path in roots:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                pytest.fail(
                    f"forbidden identifier {node.id!r} in {path}:{node.lineno}"
                )
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                pytest.fail(
                    f"forbidden attribute {node.attr!r} in {path}:{node.lineno}"
                )


def test_llm_cannot_promote_model_capability_check() -> None:
    """Capability check (Phase 4 follow-up): the LLM-facing module must
    not import any module that imports the promotion/registry modules.

    A name check (test_llm_cannot_promote_model_static_check) can be
    bypassed by `getattr(importlib.import_module('hft3.validation.certification_registry'), 'save_registry')`.
    A capability check walks source imports and asserts that the LLM-facing
    modules have no direct or transitive path to the promotion surface.
    """
    import hft3.validation.certification_registry as cert_registry
    import hft3.validation.promotion_gate as promo_gate

    # Confirm the LLM cannot reach these via direct getattr either
    forbidden_callables = {
        "save_registry": cert_registry.save_registry,
        "load_registry": cert_registry.load_registry,
        "evaluate_promotion_gate": promo_gate.evaluate_promotion_gate,
        "write_promotion_gate_report": promo_gate.write_promotion_gate_report,
    }
    # Verify they exist (else the test is meaningless)
    for name, fn in forbidden_callables.items():
        assert fn is not None, f"{name} should be defined"

    llm_roots = [
        "research_pipeline.llm",
        "research_pipeline.document_ingestion",
        "research_pipeline.intake_bundle",
        "research_pipeline.intake_schema",
        "research_pipeline.extractors",
    ]
    forbidden_modules = {
        "hft3.validation.certification_registry",
        "hft3.validation.promotion_gate",
        "hft3.validation.certification_runner",
        "hft3.validation.fast_gate_report",
    }
    package_root = Path("packages")

    def module_path(module_name: str) -> Path | None:
        parts = module_name.split(".")
        module_file = package_root.joinpath(*parts).with_suffix(".py")
        if module_file.is_file():
            return module_file
        package_file = package_root.joinpath(*parts, "__init__.py")
        if package_file.is_file():
            return package_file
        return None

    def is_forbidden(module_name: str) -> bool:
        return any(
            module_name == forbidden or module_name.startswith(f"{forbidden}.")
            for forbidden in forbidden_modules
        )

    def import_from_base(module_name: str, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module

        current_path = module_path(module_name)
        package_parts = module_name.split(".")
        if current_path is None or current_path.name != "__init__.py":
            package_parts = package_parts[:-1]
        if node.level > len(package_parts) + 1:
            return None
        base_parts = package_parts[: len(package_parts) - node.level + 1]
        if node.module:
            base_parts.extend(node.module.split("."))
        return ".".join(base_parts) if base_parts else None

    def imported_modules(module_name: str, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base_module = import_from_base(module_name, node)
                if not base_module:
                    continue
                modules.add(base_module)
                for alias in node.names:
                    imported = f"{base_module}.{alias.name}"
                    if module_path(imported) is not None or is_forbidden(imported):
                        modules.add(imported)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if is_forbidden(node.value):
                    modules.add(node.value)
        return modules

    # Collect source-level imports transitively. A runtime dir() walk misses
    # aliases like `import hft3.validation.certification_registry as reg`.
    visited: set[str] = set()
    stack: list[str] = list(llm_roots)
    while stack:
        mod_name = stack.pop()
        if mod_name in visited:
            continue
        visited.add(mod_name)
        if is_forbidden(mod_name):
            pytest.fail(
                f"LLM-facing module {mod_name} is the forbidden promotion surface"
            )
        path = module_path(mod_name)
        if path is None:
            continue
        for imported in imported_modules(mod_name, path):
            if is_forbidden(imported):
                pytest.fail(
                    f"LLM-facing module {mod_name} imports forbidden promotion surface {imported}"
                )
            if module_path(imported) is not None and imported not in visited:
                stack.append(imported)

    leaked = {module for module in visited if is_forbidden(module)}
    assert not leaked, (
        f"LLM-facing module closure leaks promotion/registry modules: {leaked}"
    )


def test_llm_promotion_attempt_at_runtime_blocked() -> None:
    """Negative test: even with a poisoned LLM payload, the intake
    pipeline has no path to the promotion surface. The bundle writer
    does not import the registry module."""
    from research_pipeline.intake_bundle import write_intake_bundle
    import sys

    # Confirm the writer's module does not import the forbidden modules
    writer_module = sys.modules[write_intake_bundle.__module__]
    writer_imports = set()
    for k in writer_module.__dict__:
        v = getattr(writer_module, k, None)
        sub_name = getattr(v, "__name__", None)
        if sub_name and sub_name.startswith("hft3.validation"):
            writer_imports.add(sub_name)
    forbidden = {
        "hft3.validation.certification_registry",
        "hft3.validation.promotion_gate",
    }
    assert not (writer_imports & forbidden), (
        f"intake_bundle writer imports promotion/registry: {writer_imports & forbidden}"
    )


def test_experiment_translation_notes_self_quarantines_when_empty() -> None:
    notes = ExperimentTranslationNotes(missing_info=[], hft3_implementation_reqs=[])
    assert notes.quarantine is True
    assert "empty_translation_notes" in notes.quarantine_reasons or any(
        "no_hft3" in r for r in notes.quarantine_reasons
    )


def test_intake_bundle_files_constant_has_14_entries() -> None:
    assert len(BUNDLE_FILES) == 14
