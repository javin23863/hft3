"""Unit tests for DatabentoResearchClient options-on-futures extensions.

Covers:
- schema validation (definition, statistics accepted; unknown rejected)
- stype_in validation (parent accepted; unknown rejected)
- estimate_cost passes schema/stype through to metadata.get_cost
- download_event_window passes schema/stype through to timeseries.get_range
- All budget gates (hard limit, operating cap) remain intact for new paths
- Manifest record includes stype_in field
- Backward compatibility: existing mbo + continuous callers unchanged

No network or real API key is required; the databento client is fully mocked.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure this worktree's packages are importable.
_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "packages"
for _p in (_REPO, _PKG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = datetime(2024, 3, 15, 14, 0, 0, tzinfo=timezone.utc)
END = datetime(2024, 3, 15, 21, 0, 0, tzinfo=timezone.utc)
SYMBOLS_CONT = ["ES.c.0"]
SYMBOLS_PARENT = ["ES.OPT"]
DATASET = "GLBX.MDP3"


def _make_client(tmp_path: Path, cost_return: float = 0.10):
    """Build a DatabentoResearchClient with all databento I/O mocked."""
    import databento as db
    from data_system.src.databento_client import DatabentoResearchClient

    with patch.object(db, "Historical") as MockHistorical:
        mock_hist = MagicMock()
        MockHistorical.return_value = mock_hist

        # metadata.get_cost returns a controlled value
        mock_hist.metadata.get_cost.return_value = cost_return
        # timeseries.get_range writes nothing (no real download needed)
        mock_hist.timeseries.get_range.return_value = None

        with patch.dict(os.environ, {"DATABENTO_API_KEY": "db-test-key"}):
            client = DatabentoResearchClient()
            # Override manifest path to tmp_path so tests are isolated
            client.manifest_path = str(tmp_path / "manifest.parquet")
            client.budget.manifest_path = str(tmp_path / "manifest.parquet")
            # Keep a reference to the mock for assertions
            client._mock_hist = mock_hist
        return client


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_validate_schema_accepts_mbo():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_schema("mbo")  # must not raise


def test_validate_schema_accepts_definition():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_schema("definition")


def test_validate_schema_accepts_statistics():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_schema("statistics")


def test_validate_schema_accepts_mbp1():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_schema("mbp-1")


def test_validate_schema_rejects_unknown():
    from data_system.src.databento_client import DatabentoResearchClient
    with pytest.raises(ValueError, match="Unknown schema"):
        DatabentoResearchClient._validate_schema("bogus_schema")


def test_validate_schema_rejects_empty_string():
    from data_system.src.databento_client import DatabentoResearchClient
    with pytest.raises(ValueError, match="Unknown schema"):
        DatabentoResearchClient._validate_schema("")


# ---------------------------------------------------------------------------
# stype_in validation
# ---------------------------------------------------------------------------


def test_validate_stype_accepts_continuous():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_stype("continuous")


def test_validate_stype_accepts_parent():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_stype("parent")


def test_validate_stype_accepts_raw_symbol():
    from data_system.src.databento_client import DatabentoResearchClient
    DatabentoResearchClient._validate_stype("raw_symbol")


def test_validate_stype_rejects_unknown():
    from data_system.src.databento_client import DatabentoResearchClient
    with pytest.raises(ValueError, match="Unknown stype_in"):
        DatabentoResearchClient._validate_stype("not_a_real_stype")


# ---------------------------------------------------------------------------
# estimate_cost: new schemas pass through correctly
# ---------------------------------------------------------------------------


def test_estimate_cost_definition_schema(tmp_path):
    client = _make_client(tmp_path, cost_return=0.25)
    cost = client.estimate_cost(
        SYMBOLS_PARENT, START, END,
        dataset=DATASET, schema="definition", stype_in="parent",
    )
    assert cost == pytest.approx(0.25)
    call_kwargs = client._mock_hist.metadata.get_cost.call_args.kwargs
    assert call_kwargs["schema"] == "definition"
    assert call_kwargs["stype_in"] == "parent"
    assert call_kwargs["symbols"] == SYMBOLS_PARENT


def test_estimate_cost_statistics_schema(tmp_path):
    client = _make_client(tmp_path, cost_return=0.07)
    cost = client.estimate_cost(
        SYMBOLS_PARENT, START, END,
        dataset=DATASET, schema="statistics", stype_in="parent",
    )
    assert cost == pytest.approx(0.07)
    call_kwargs = client._mock_hist.metadata.get_cost.call_args.kwargs
    assert call_kwargs["schema"] == "statistics"
    assert call_kwargs["stype_in"] == "parent"


def test_estimate_cost_rejects_bad_schema(tmp_path):
    client = _make_client(tmp_path)
    with pytest.raises(ValueError, match="Unknown schema"):
        client.estimate_cost(SYMBOLS_PARENT, START, END, schema="bad_schema")


def test_estimate_cost_rejects_bad_stype(tmp_path):
    client = _make_client(tmp_path)
    with pytest.raises(ValueError, match="Unknown stype_in"):
        client.estimate_cost(SYMBOLS_PARENT, START, END, stype_in="unknown_stype")


# ---------------------------------------------------------------------------
# download_event_window: new schemas/stypes flow through end to end
# ---------------------------------------------------------------------------


def test_download_definition_schema_parent_stype(tmp_path):
    """definition schema + parent stype: cost-check, download, manifest all run."""
    client = _make_client(tmp_path, cost_return=0.05)

    dest = client.download_event_window(
        event_id="test_def_001",
        symbols=SYMBOLS_PARENT,
        start_utc=START,
        end_utc=END,
        dataset=DATASET,
        schema="definition",
        stype_in="parent",
        output_path=str(tmp_path / "test_def_001_definition.dbn.zst"),
    )

    # timeseries.get_range must have been called with the right args
    call_kwargs = client._mock_hist.timeseries.get_range.call_args.kwargs
    assert call_kwargs["schema"] == "definition"
    assert call_kwargs["stype_in"] == "parent"
    assert call_kwargs["symbols"] == SYMBOLS_PARENT
    assert dest == str(tmp_path / "test_def_001_definition.dbn.zst")


def test_download_statistics_schema_parent_stype(tmp_path):
    client = _make_client(tmp_path, cost_return=0.04)

    dest = client.download_event_window(
        event_id="test_stat_001",
        symbols=["NQ.OPT"],
        start_utc=START,
        end_utc=END,
        dataset=DATASET,
        schema="statistics",
        stype_in="parent",
        output_path=str(tmp_path / "test_stat_001_statistics.dbn.zst"),
    )

    call_kwargs = client._mock_hist.timeseries.get_range.call_args.kwargs
    assert call_kwargs["schema"] == "statistics"
    assert call_kwargs["stype_in"] == "parent"
    assert call_kwargs["symbols"] == ["NQ.OPT"]


def test_download_rejects_unknown_schema_before_api_call(tmp_path):
    client = _make_client(tmp_path)
    with pytest.raises(ValueError, match="Unknown schema"):
        client.download_event_window(
            event_id="bad_schema_test",
            symbols=SYMBOLS_PARENT,
            start_utc=START,
            end_utc=END,
            schema="nonexistent",
            stype_in="parent",
        )
    # metadata.get_cost must NOT have been called
    client._mock_hist.metadata.get_cost.assert_not_called()


def test_download_rejects_unknown_stype_before_api_call(tmp_path):
    client = _make_client(tmp_path)
    with pytest.raises(ValueError, match="Unknown stype_in"):
        client.download_event_window(
            event_id="bad_stype_test",
            symbols=SYMBOLS_PARENT,
            start_utc=START,
            end_utc=END,
            schema="definition",
            stype_in="totally_wrong",
        )
    client._mock_hist.metadata.get_cost.assert_not_called()


# ---------------------------------------------------------------------------
# Budget gating is preserved for new schema paths
# ---------------------------------------------------------------------------


def test_hard_limit_blocks_definition_schema(tmp_path):
    """A cost above HARD_LIMIT with definition schema must be blocked."""
    from data_system.src.budget_manager import BudgetManager
    cost_above_limit = BudgetManager.HARD_LIMIT + 1.0
    client = _make_client(tmp_path, cost_return=cost_above_limit)

    with pytest.raises(ValueError, match="hard limit"):
        client.download_event_window(
            event_id="expensive_def",
            symbols=SYMBOLS_PARENT,
            start_utc=START,
            end_utc=END,
            schema="definition",
            stype_in="parent",
        )
    # timeseries.get_range must NOT have been called
    client._mock_hist.timeseries.get_range.assert_not_called()


def test_operating_cap_blocks_statistics_schema(tmp_path):
    """Accumulated spend near OPERATING_CAP blocks new statistics pull."""
    from data_system.src.budget_manager import BudgetManager
    import pandas as pd

    # Pre-fill manifest so total_used is just below operating cap
    manifest_path = str(tmp_path / "manifest.parquet")
    prior_cost = BudgetManager.OPERATING_CAP - 0.50
    df = pd.DataFrame([{
        "event_id": "prior_event",
        "cost": prior_cost,
        "symbols": "['ES.c.0']",
        "requested_symbol": "ES.c.0",
        "resolved_symbol": "ES.c.0",
        "start_utc": START,
        "end_utc": END,
        "duration_seconds": 25200.0,
        "cost_per_symbol_minute": prior_cost / 420,
        "output_path": "data/prior.dbn.zst",
        "dataset": DATASET,
        "schema": "mbo",
        "stype_in": "continuous",
        "download_time": datetime.now(timezone.utc),
    }])
    df.to_parquet(manifest_path)

    # New pull would cost $1.00, pushing total over cap
    client = _make_client(tmp_path, cost_return=1.0)
    client.manifest_path = manifest_path
    client.budget.manifest_path = manifest_path
    client.budget.total_used = prior_cost

    with pytest.raises(ValueError, match="operating cap"):
        client.download_event_window(
            event_id="stats_over_cap",
            symbols=SYMBOLS_PARENT,
            start_utc=START,
            end_utc=END,
            schema="statistics",
            stype_in="parent",
        )
    client._mock_hist.timeseries.get_range.assert_not_called()


def test_override_hard_limit_allows_expensive_definition_pull(tmp_path):
    """override_hard_limit=True lets an expensive definition pull proceed."""
    from data_system.src.budget_manager import BudgetManager
    cost_above_limit = BudgetManager.HARD_LIMIT + 1.0
    client = _make_client(tmp_path, cost_return=cost_above_limit)

    dest = client.download_event_window(
        event_id="expensive_def_override",
        symbols=SYMBOLS_PARENT,
        start_utc=START,
        end_utc=END,
        schema="definition",
        stype_in="parent",
        output_path=str(tmp_path / "out.dbn.zst"),
        override_hard_limit=True,
        override_operating_cap=True,
    )
    client._mock_hist.timeseries.get_range.assert_called_once()
    assert dest == str(tmp_path / "out.dbn.zst")


# ---------------------------------------------------------------------------
# Manifest records stype_in field
# ---------------------------------------------------------------------------


def test_manifest_records_stype_in(tmp_path):
    import pandas as pd

    client = _make_client(tmp_path, cost_return=0.03)
    manifest_path = str(tmp_path / "manifest.parquet")
    client.manifest_path = manifest_path
    client.budget.manifest_path = manifest_path

    client.download_event_window(
        event_id="manifest_test",
        symbols=["ES.OPT"],
        start_utc=START,
        end_utc=END,
        schema="definition",
        stype_in="parent",
        output_path=str(tmp_path / "manifest_test.dbn.zst"),
    )

    df = pd.read_parquet(manifest_path)
    assert "stype_in" in df.columns
    assert df.iloc[0]["stype_in"] == "parent"
    assert df.iloc[0]["schema"] == "definition"


# ---------------------------------------------------------------------------
# Backward compatibility: mbo + continuous unchanged
# ---------------------------------------------------------------------------


def test_backward_compat_mbo_continuous(tmp_path):
    """Existing mbo/continuous path still works identically."""
    client = _make_client(tmp_path, cost_return=0.08)

    dest = client.download_event_window(
        event_id="legacy_mbo",
        symbols=SYMBOLS_CONT,
        start_utc=START,
        end_utc=END,
        dataset=DATASET,
        schema="mbo",
        stype_in="continuous",
        output_path=str(tmp_path / "legacy_mbo.dbn.zst"),
    )

    call_kwargs = client._mock_hist.timeseries.get_range.call_args.kwargs
    assert call_kwargs["schema"] == "mbo"
    assert call_kwargs["stype_in"] == "continuous"
    assert dest == str(tmp_path / "legacy_mbo.dbn.zst")


def test_backward_compat_default_args(tmp_path):
    """estimate_cost with no schema/stype args uses mbo/continuous defaults."""
    client = _make_client(tmp_path, cost_return=0.02)
    client.estimate_cost(SYMBOLS_CONT, START, END)
    call_kwargs = client._mock_hist.metadata.get_cost.call_args.kwargs
    assert call_kwargs["schema"] == "mbo"
    assert call_kwargs["stype_in"] == "continuous"
