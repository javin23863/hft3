"""Phase 5 — autoresearch VectorBT must use optimized in-process matrix path."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np

from backtest_pipeline.src.promotion_gate import PromotionGate
from backtest_pipeline.src.vectorbt_adapter import FilterResult, filter_candidates
from research_pipeline.generation_loop import AutoresearchConfig, _run_vectorbt_screen
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.model_generation import generate_candidates
from research_pipeline.types import CandidateModel, ParsedHypothesis


@dataclass
class _FakeFilterResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _fake_persist(artifact, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(artifact)
    payload.setdefault("screening_artifact_hash", "phase5_perf_stub")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _minimal_parsed() -> ParsedHypothesis:
    return parse_hypothesis(
        "Fade spread blowout recompression on CPI tight events",
        use_llm=False,
    )


def _one_candidate() -> list[CandidateModel]:
    parsed = _minimal_parsed()
    return list(
        generate_candidates(
            parsed,
            max_candidates=1,
            expand_for_vectorbt=True,
            target_event_id="CPI_2024_09_11_TIGHT",
            target_symbol="MES",
        )
    )[:1]


class TestAutoresearchVectorBTPerformance:
    def test_filter_candidates_uses_matrix_path_without_subprocess(self, monkeypatch, tmp_path):
        pipeline_subprocess_calls: list[list[str]] = []

        def _track_subprocess(*args, **kwargs):
            cmd = [str(c) for c in (args[0] if args else kwargs.get("args", []))]
            if any("run_pipeline.py" in part for part in cmd):
                pipeline_subprocess_calls.append(cmd)
            raise AssertionError(f"unexpected subprocess: {cmd}")

        monkeypatch.setattr(subprocess, "run", _track_subprocess)
        monkeypatch.setattr(subprocess, "check_output", _track_subprocess)

        parsed = _minimal_parsed()
        candidates = list(
            generate_candidates(
                parsed,
                max_candidates=2,
                expand_for_vectorbt=True,
                target_event_id="CPI_2024_09_11_TIGHT",
                target_symbol="MES",
            )
        )[:2]
        ohlcv = np.column_stack(
            [
                np.arange(40, dtype=float),
                np.ones(40),
                np.ones(40),
                np.ones(40),
                np.ones(40),
                np.ones(40),
            ]
        )

        matrix_mock = MagicMock(
            return_value=FilterResult(
                backend="vectorbt",
                run_id="matrix_stub",
                total_candidates=len(candidates),
                screening_scope="pilot",
            )
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_matrix.run_vectorbt_simulation_matrix",
            matrix_mock,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter._default_data_loader",
            lambda event_id, repo_root, symbol=None: ohlcv,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.fs_v1_screen_path.resolve_fs_v1_screen_context",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter.apply_promotion_gates",
            lambda result, **kwargs: result,
        )

        result = filter_candidates(
            candidates=candidates,
            parsed=parsed,
            event_id="CPI_2024_09_11_TIGHT",
            repo_root=tmp_path,
            gates=PromotionGate(min_trades=0),
            screening_scope="pilot",
            prefer_fs_v1_path=False,
        )

        assert pipeline_subprocess_calls == []
        assert matrix_mock.called
        assert result.screen_performance.get("screening_path") == "matrix_v2"
        assert result.screen_performance.get("subprocess_per_unit") == 0

    def test_run_vectorbt_screen_records_performance_counters(self, tmp_path):
        candidates = _one_candidate()
        parsed = _minimal_parsed()
        perf_payload = {
            "screening_path": "matrix_v2",
            "subprocess_per_unit": 0,
            "feature_store_load_count": 0,
            "raw_signal_computations": 1,
            "portfolio_call_count": 2,
            "matrix_chunk_size": 256,
            "native_thread_limits": {"OMP_NUM_THREADS": "1"},
        }

        def _filter_with_perf(**kwargs):
            return _FakeFilterResult(
                {
                    "run_id": "autoresearch_perf",
                    "backend": "vectorbt",
                    "screening_scope": kwargs["screening_scope"],
                    "screen_performance": perf_payload,
                    "promoted": [],
                    "rejected": [],
                }
            )

        cfg = AutoresearchConfig(screening_scope="pilot", symbol="MES")
        artifact_dir = tmp_path / "gen_000"
        artifact_dir.mkdir()

        screening, path = _run_vectorbt_screen(
            candidates=candidates,
            parsed=parsed,
            event_id="CPI_2024_09_11_TIGHT",
            repo_root=tmp_path,
            cfg=cfg,
            artifact_dir=artifact_dir,
            filter_fn=_filter_with_perf,
            persist_fn=_fake_persist,
        )

        assert path.is_file()
        assert screening["screen_performance"]["subprocess_per_unit"] == 0
        assert screening["screen_performance"]["screening_path"] == "matrix_v2"

    def test_run_vectorbt_screen_never_subprocesses_run_pipeline(self, monkeypatch, tmp_path):
        pipeline_subprocess_calls: list[list[str]] = []

        def _track_subprocess(*args, **kwargs):
            cmd = [str(c) for c in (args[0] if args else kwargs.get("args", []))]
            if any("run_pipeline.py" in part for part in cmd):
                pipeline_subprocess_calls.append(cmd)
            return b"deadbeef\n"

        monkeypatch.setattr(subprocess, "run", _track_subprocess)
        monkeypatch.setattr(subprocess, "check_output", _track_subprocess)

        def _filter_inproc(**kwargs):
            return _FakeFilterResult(
                {
                    "run_id": "matrix_inproc",
                    "backend": "vectorbt",
                    "screening_scope": kwargs["screening_scope"],
                    "screen_performance": {
                        "screening_path": "matrix_v2",
                        "subprocess_per_unit": 0,
                    },
                    "promoted": [],
                    "rejected": [],
                }
            )

        cfg = AutoresearchConfig(screening_scope="pilot", symbol="MES")
        artifact_dir = tmp_path / "gen_001"
        artifact_dir.mkdir()
        _run_vectorbt_screen(
            candidates=_one_candidate(),
            parsed=_minimal_parsed(),
            event_id="CPI_2024_09_11_TIGHT",
            repo_root=tmp_path,
            cfg=cfg,
            artifact_dir=artifact_dir,
            filter_fn=_filter_inproc,
            persist_fn=_fake_persist,
        )

        assert pipeline_subprocess_calls == []
