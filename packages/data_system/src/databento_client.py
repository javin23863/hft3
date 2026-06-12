import os
import databento as db
from .budget_manager import BudgetManager
from .manifest_io import append_manifest_record
from datetime import datetime, timezone

# Schemas supported for cost estimation and download.
# Fail-closed: any value not in this set is rejected before touching the API.
_SUPPORTED_SCHEMAS = frozenset({
    "mbo", "mbp-1", "mbp-10", "trades",
    "ohlcv-1s", "ohlcv-1m", "ohlcv-1h", "ohlcv-1d",
    "definition", "statistics",
})

# stype_in values accepted.  "parent" enables options-on-futures symbology
# (e.g. ES.OPT, NQ.OPT); "continuous" is the legacy default.
_SUPPORTED_STYPES = frozenset({"continuous", "parent", "raw_symbol", "instrument_id"})

class DatabentoResearchClient:
    """
    Controlled Databento downloader using metadata.get_cost for budget gating.
    Records metadata in manifest.parquet.

    Schema support
    --------------
    ``schema`` accepts any value in ``_SUPPORTED_SCHEMAS``.  The options-on-futures
    workflow uses ``"definition"`` (instrument reference data) and ``"statistics"``
    (daily open-interest / settlement) in addition to the existing ``"mbo"`` path.

    stype_in support
    ----------------
    ``stype_in`` accepts ``"parent"`` (e.g. ``ES.OPT``, ``NQ.OPT``) in addition to
    the existing ``"continuous"`` default.  Unknown values are rejected before any
    API call is made (fail-closed).
    """
    def __init__(self, api_key: str = None):
        # Pull from the single master keys store (Desktop keys.env + repo .env)
        # so credentials live in one place. Already-exported env vars still win.
        if not api_key and not os.getenv("DATABENTO_API_KEY"):
            try:
                from .keystore import load_keys
                load_keys()
            except Exception:
                pass
        self.api_key = api_key or os.getenv("DATABENTO_API_KEY")
        if not self.api_key:
            raise ValueError("DATABENTO_API_KEY must be set")

        self.client = db.Historical(self.api_key)
        # HFT3_MANIFEST_PATH points at the canonical lake ledger so spend
        # accounting survives cwd changes and multiple checkouts.
        self.manifest_path = os.environ.get("HFT3_MANIFEST_PATH", "data/manifest.parquet")
        self.budget = BudgetManager(self.manifest_path)

    # ------------------------------------------------------------------
    # Internal validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_schema(schema: str) -> None:
        """Raise ValueError for any schema not in the supported set."""
        if schema not in _SUPPORTED_SCHEMAS:
            raise ValueError(
                f"Unknown schema {schema!r}. "
                f"Supported schemas: {sorted(_SUPPORTED_SCHEMAS)}"
            )

    @staticmethod
    def _validate_stype(stype_in: str) -> None:
        """Raise ValueError for any stype_in not in the supported set."""
        if stype_in not in _SUPPORTED_STYPES:
            raise ValueError(
                f"Unknown stype_in {stype_in!r}. "
                f"Supported stype_in values: {sorted(_SUPPORTED_STYPES)}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_cost(
        self,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        schema="mbo",
        stype_in: str = "continuous",
    ) -> float:
        """Return the Databento cost estimate in USD for the requested pull.
        Validates ``schema`` and ``stype_in`` against the supported sets and raises
        ``ValueError`` for unknown values before touching the API."""
        self._validate_schema(schema)
        self._validate_stype(stype_in)
        return float(
            self.client.metadata.get_cost(
                dataset=dataset,
                schema=schema,
                symbols=symbols,
                stype_in=stype_in,
                start=start_utc,
                end=end_utc,
            )
        )

    def download_event_window(
        self,
        event_id: str,
        symbols: list,
        start_utc: datetime,
        end_utc: datetime,
        dataset="GLBX.MDP3",
        schema="mbo",
        stype_in: str = "continuous",
        requested_symbol: str | None = None,
        output_path: str | None = None,
        override_hard_limit: bool = False,
        override_operating_cap: bool = False,
        cost_estimate: float | None = None,
    ) -> str:
        """Cost-estimate, budget-gate, and download one time-series window.
        Validates ``schema`` and ``stype_in`` before any API call.
        All budget paths (hard limit, operating cap) are preserved exactly as before.
        ``cost_estimate`` lets callers that already priced the window (e.g. the MBO
        release lane via ``resolve_download_symbol``) skip a duplicate metadata call.
        Returns the local path to the downloaded file."""
        # Fail closed: validate before cost estimation or download.
        self._validate_schema(schema)
        self._validate_stype(stype_in)
        if cost_estimate is None:
            cost_estimate = self.estimate_cost(
                symbols, start_utc, end_utc, dataset, schema, stype_in
            )

        if override_operating_cap:
            if cost_estimate > self.budget.HARD_LIMIT and not override_hard_limit:
                raise ValueError(
                    f"Cost estimate ${cost_estimate:.2f} exceeds hard limit "
                    f"${self.budget.HARD_LIMIT:.2f}"
                )
        else:
            self.budget.check_request(cost_estimate, override_hard_limit=override_hard_limit)

        dest = output_path or f"data/{event_id}_{schema}.dbn.zst"
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

        self.client.timeseries.get_range(
            dataset=dataset,
            schema=schema,
            symbols=symbols,
            stype_in=stype_in,
            start=start_utc,
            end=end_utc,
            path=dest,
        )

        metrics = self.budget.calculate_cost_metrics(
            cost_estimate, len(symbols), start_utc, end_utc
        )

        resolved_symbol = symbols[0] if symbols else ""
        record = {
            "event_id": event_id,
            "symbols": str(symbols),
            "requested_symbol": requested_symbol or resolved_symbol,
            "resolved_symbol": resolved_symbol,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "cost": cost_estimate,
            "duration_seconds": metrics["duration_seconds"],
            "cost_per_symbol_minute": metrics["cost_per_symbol_minute"],
            "output_path": dest,
            "dataset": dataset,
            "schema": schema,
            "stype_in": stype_in,
            "download_time": datetime.now(timezone.utc),
        }

        self._record_manifest(record)
        return dest

    def _record_manifest(self, record: dict) -> None:
        # Atomic, cross-process-safe append (file lock + tmp-file replace) —
        # replaces the old non-atomic read/concat/write that could drop rows
        # under concurrent downloads.
        append_manifest_record(self.manifest_path, record)
