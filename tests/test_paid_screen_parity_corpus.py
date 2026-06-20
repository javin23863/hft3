"""Tests for the fixed parity corpus fixture."""
import pytest
import json
import os
import tempfile

from tests.fixtures.paid_screen_parity_corpus import (
    build_parity_corpus, write_corpus_jsonl, ParityCorpusUnit,
    CME_M6_SYMBOLS, PARITY_EVENTS, PARITY_MODELS,
    SPARSE_EVENTS, DENSE_EVENTS, MISSING_DATA_EVENTS, MISSING_MODEL_IDS,
)


class TestParityCorpusStructure:
    def test_corpus_is_deterministic(self):
        """Building the corpus twice must produce identical results."""
        c1 = build_parity_corpus()
        c2 = build_parity_corpus()
        assert len(c1) == len(c2)
        for u1, u2 in zip(c1, c2):
            assert u1.unit_id == u2.unit_id
            assert u1.model_id == u2.model_id
            assert u1.symbol == u2.symbol
            assert u1.event_id == u2.event_id

    def test_corpus_has_at_least_20_events(self):
        corpus = build_parity_corpus()
        event_ids = {u.event_id for u in corpus}
        assert len(event_ids) >= 20, f"Only {len(event_ids)} events in corpus"

    def test_corpus_covers_all_m6_symbols(self):
        corpus = build_parity_corpus()
        symbols = {u.symbol for u in corpus}
        for s in CME_M6_SYMBOLS:
            assert s in symbols, f"Symbol {s} not covered in corpus"

    def test_corpus_has_at_least_20_models(self):
        corpus = build_parity_corpus()
        model_ids = {u.model_id for u in corpus}
        assert len(model_ids) >= 20, f"Only {len(model_ids)} models in corpus"

    def test_corpus_includes_sparse_data_events(self):
        corpus = build_parity_corpus()
        event_ids = {u.event_id for u in corpus}
        for e in SPARSE_EVENTS:
            assert e in event_ids, f"Sparse event {e} not in corpus"

    def test_corpus_includes_dense_data_events(self):
        corpus = build_parity_corpus()
        event_ids = {u.event_id for u in corpus}
        for e in DENSE_EVENTS:
            assert e in event_ids, f"Dense event {e} not in corpus"

    def test_corpus_includes_missing_data_outcomes(self):
        corpus = build_parity_corpus()
        outcomes = {u.expected_outcome for u in corpus}
        assert "missing_data" in outcomes

    def test_corpus_includes_missing_model_outcomes(self):
        corpus = build_parity_corpus()
        outcomes = {u.expected_outcome for u in corpus}
        assert "missing_model" in outcomes

    def test_corpus_includes_budget_exhaustion(self):
        corpus = build_parity_corpus()
        outcomes = {u.expected_outcome for u in corpus}
        assert "budget_exhausted" in outcomes

    def test_all_units_have_required_fields(self):
        corpus = build_parity_corpus()
        for u in corpus:
            assert u.unit_id, "unit_id missing"
            assert u.model_id, "model_id missing"
            assert u.symbol, "symbol missing"
            assert u.event_id, "event_id missing"
            assert u.event_type, "event_type missing"
            assert u.thesis, "thesis missing"

    def test_unit_ids_are_unique(self):
        corpus = build_parity_corpus()
        ids = [u.unit_id for u in corpus]
        assert len(ids) == len(set(ids)), "Duplicate unit_ids in corpus"

    def test_corpus_is_not_empty(self):
        corpus = build_parity_corpus()
        assert len(corpus) > 0


class TestParityCorpusPersistence:
    def test_write_and_read_jsonl(self, tmp_path):
        path = str(tmp_path / "parity_corpus.jsonl")
        count = write_corpus_jsonl(path)
        assert count > 0
        assert os.path.exists(path)

        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == count

        for line in lines:
            row = json.loads(line.strip())
            assert "unit_id" in row
            assert "model_id" in row
            assert "symbol" in row
            assert "event_id" in row
            assert "event_type" in row
            assert "thesis" in row
            assert "expected_outcome" in row

    def test_jsonl_is_sortable(self, tmp_path):
        """JSONL lines must be independently parseable (no trailing data)."""
        path = str(tmp_path / "parity_corpus.jsonl")
        write_corpus_jsonl(path)
        with open(path) as f:
            for line in f:
                row = json.loads(line.strip())
                assert isinstance(row, dict)


class TestParityCorpusDimensions:
    def test_parameter_grid_is_represented(self):
        """The corpus should exercise the full parameter grid through the models.
        Each model x symbol x event combination will run through the 256-combo
        parameter grid. We verify that enough combinations exist."""
        corpus = build_parity_corpus()
        assert len(corpus) >= 100, f"Corpus too small: {len(corpus)} units"

    def test_strategy_families_represented(self):
        """Models HYP_1 through HYP_25 cover different strategy families."""
        corpus = build_parity_corpus()
        model_ids = {u.model_id for u in corpus}
        for i in range(1, 26):
            assert f"HYP_{i}" in model_ids, f"HYP_{i} not in corpus models"