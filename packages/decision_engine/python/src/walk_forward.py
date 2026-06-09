"""Walk-forward validator and weight exporter for the HFT3 decision engine.

Design notes
------------
Rolling refit
    Each evaluation segment is scored by a model trained *only* on data strictly
    before that segment.  Two window schemes are supported via ``window_mode``:

    - ``"expanding"`` (default): training set grows from the earliest available
      data up to (but not including) the evaluation segment.
    - ``"sliding"``: training set is a fixed-length window ending just before
      the evaluation segment.  Set ``sliding_window_years`` to control width.

Kill-gate (Discovery stage)
    The Discovery period (2018-2020) is split internally: the first two thirds
    of that data form the Discovery training set; the final third is held out as
    an OOS validation slice.  The kill-gate fires on that OOS slice, never on
    the same data used to fit the model.

Purge / embargo
    ``purge_days`` rows are dropped from the *tail* of the training data before
    every boundary to avoid label-horizon overlap.  ``embargo_days`` rows are
    skipped from the *head* of the evaluation data for the same reason.  Both
    default to 0 (backward-compatible).  The ``data_loader`` callable receives
    two optional keyword arguments ``purge_end_date`` and ``embargo_start_date``
    (ISO-8601 strings); loaders that accept only ``(start_year, end_year)`` are
    detected and called without these kwargs, so existing callers are unaffected.

Return dict
    Same keys as the original plus new keys (never removed):
    - Per-period dicts now contain ``"train_start_year"``, ``"train_end_year"``,
      ``"oos"``: True/False, ``"purge_days"``, ``"embargo_days"``.
    - Top-level ``"status"`` is still ``"PASS"`` / ``"FAIL"``.
    - ``"model"`` is included on PASS (the model produced by the final refit on
      all pre-Recent-holdout data, matching original behaviour).
    - New top-level key ``"window_mode"`` records the scheme used.
"""
from __future__ import annotations

import inspect
import struct
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, List, Optional


@dataclass
class ValidationPeriod:
    name: str
    start_year: int
    end_year: int


class WalkForwardValidator:
    """Rolling walk-forward validator.

    Enforces the strict walk-forward validation rules from the blueprint:

    Discovery      2018-2020  (kill-gate on an internal OOS split)
    Confirmation   2021-2022
    Holdout        2023-2024
    Recent holdout 2025

    Parameters
    ----------
    window_mode:
        ``"expanding"`` (default) or ``"sliding"``.
    sliding_window_years:
        Width of the sliding training window in years.  Ignored when
        ``window_mode="expanding"``.
    purge_days:
        Number of calendar days to drop from the tail of training data at every
        train/eval boundary.
    embargo_days:
        Number of calendar days to skip from the head of evaluation data at
        every train/eval boundary.
    discovery_validation_fraction:
        Fraction of the Discovery period reserved as an internal OOS validation
        slice for the Discovery kill-gate.  Default 0.33 (last one-third of
        2018-2020 data).
    """

    def __init__(
        self,
        *,
        window_mode: str = "expanding",
        sliding_window_years: int = 3,
        purge_days: int = 0,
        embargo_days: int = 0,
        discovery_validation_fraction: float = 0.33,
    ) -> None:
        if window_mode not in ("expanding", "sliding"):
            raise ValueError(f"window_mode must be 'expanding' or 'sliding', got {window_mode!r}")
        self.window_mode = window_mode
        self.sliding_window_years = sliding_window_years
        self.purge_days = purge_days
        self.embargo_days = embargo_days
        self.discovery_validation_fraction = discovery_validation_fraction

        # Named evaluation segments — kept for downstream consumers that
        # inspect `validator.periods`.
        self.periods: List[ValidationPeriod] = [
            ValidationPeriod("Discovery", 2018, 2020),
            ValidationPeriod("Confirmation", 2021, 2022),
            ValidationPeriod("Holdout", 2023, 2024),
            ValidationPeriod("Recent holdout", 2025, 2025),
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_validation(
        self,
        train_func: Callable[[Any], Any],
        eval_func: Callable[[Any, Any], dict],
        data_loader: Callable[..., Any],
    ) -> dict:
        """Execute the rolling walk-forward validation sequence.

        A model must pass each stage before moving to the next.  For each
        evaluation segment the model is trained only on data strictly before
        that segment (rolling refit).

        Parameters
        ----------
        train_func:
            ``train_func(data) -> model``
        eval_func:
            ``eval_func(model, data) -> metric_dict``
            Must include ``"net_expectancy"`` key.
        data_loader:
            ``data_loader(start_year, end_year, *, purge_end_date=None,
            embargo_start_date=None) -> data``

            Loaders that only accept positional ``(start_year, end_year)`` are
            detected via signature inspection and called without the keyword
            arguments.
        """
        loader_accepts_dates = _loader_accepts_date_kwargs(data_loader)

        results: dict[str, Any] = {"window_mode": self.window_mode}
        last_model = None

        for period in self.periods:
            eval_start_year = period.start_year
            eval_end_year = period.end_year

            # --- Determine training window -----------------------------------
            train_start_year, train_end_year = self._training_window(
                eval_start_year
            )

            # --- Purge / embargo date strings --------------------------------
            purge_end_date: Optional[str] = None
            embargo_start_date: Optional[str] = None
            if self.purge_days > 0:
                boundary = date(eval_start_year, 1, 1)
                purge_cutoff = boundary - timedelta(days=self.purge_days)
                purge_end_date = purge_cutoff.isoformat()
            if self.embargo_days > 0:
                boundary = date(eval_start_year, 1, 1)
                embargo_after = boundary + timedelta(days=self.embargo_days)
                embargo_start_date = embargo_after.isoformat()

            # --- Load training data and fit model ----------------------------
            train_data = _call_loader(
                data_loader,
                train_start_year,
                train_end_year,
                loader_accepts_dates,
                purge_end_date=purge_end_date,
                embargo_start_date=None,  # embargo only applies to eval side
            )
            model = train_func(train_data)

            # --- Discovery: internal OOS kill-gate ---------------------------
            if period.name == "Discovery":
                gate_metric, discovery_result = self._discovery_gate(
                    train_func,
                    eval_func,
                    data_loader,
                    loader_accepts_dates,
                    purge_end_date,
                    embargo_start_date,
                )
                results[period.name] = discovery_result
                if gate_metric.get("net_expectancy", 0) <= 0:
                    results["status"] = "FAIL"
                    return results
                last_model = model
                continue

            # --- Load evaluation data (OOS) ----------------------------------
            eval_data = _call_loader(
                data_loader,
                eval_start_year,
                eval_end_year,
                loader_accepts_dates,
                purge_end_date=None,  # purge already applied to train side
                embargo_start_date=embargo_start_date,
            )
            metric = eval_func(model, eval_data)

            period_result = dict(metric)
            period_result["train_start_year"] = train_start_year
            period_result["train_end_year"] = train_end_year
            period_result["oos"] = True
            period_result["purge_days"] = self.purge_days
            period_result["embargo_days"] = self.embargo_days
            results[period.name] = period_result

            if metric.get("net_expectancy", 0) <= 0:
                results["status"] = "FAIL"
                return results

            last_model = model

        results["status"] = "PASS"
        results["model"] = last_model
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _training_window(self, eval_start_year: int) -> tuple[int, int]:
        """Return (train_start_year, train_end_year) for a given eval start."""
        train_end_year = eval_start_year - 1
        if self.window_mode == "expanding":
            # Expanding: always start from the beginning of recorded history.
            train_start_year = self.periods[0].start_year
        else:
            # Sliding: fixed-width window ending just before eval.
            train_start_year = train_end_year - self.sliding_window_years + 1
        return train_start_year, train_end_year

    def _discovery_gate(
        self,
        train_func: Callable,
        eval_func: Callable,
        data_loader: Callable,
        loader_accepts_dates: bool,
        purge_end_date: Optional[str],
        embargo_start_date: Optional[str],
    ) -> tuple[dict, dict]:
        """Discovery kill-gate using an internal OOS validation split.

        The Discovery period (2018-2020) is split so the final
        ``discovery_validation_fraction`` of calendar time is held out.
        The model is trained on the train-split only and evaluated on the
        held-out split.  Neither split overlaps the model fit.

        Returns (gate_metric, full_result_dict).
        """
        disc_period = self.periods[0]  # Discovery 2018-2020
        total_years = disc_period.end_year - disc_period.start_year + 1  # 3
        val_years = max(1, round(total_years * self.discovery_validation_fraction))
        # train: start_year .. (end_year - val_years)
        # val:   (end_year - val_years + 1) .. end_year
        disc_train_end = disc_period.end_year - val_years
        disc_val_start = disc_train_end + 1
        disc_val_end = disc_period.end_year

        disc_train_data = _call_loader(
            data_loader,
            disc_period.start_year,
            disc_train_end,
            loader_accepts_dates,
            purge_end_date=purge_end_date,
            embargo_start_date=None,
        )
        disc_model = train_func(disc_train_data)

        disc_val_data = _call_loader(
            data_loader,
            disc_val_start,
            disc_val_end,
            loader_accepts_dates,
            purge_end_date=None,
            embargo_start_date=embargo_start_date,
        )
        gate_metric = eval_func(disc_model, disc_val_data)

        result = dict(gate_metric)
        result["train_start_year"] = disc_period.start_year
        result["train_end_year"] = disc_train_end
        result["discovery_val_start_year"] = disc_val_start
        result["discovery_val_end_year"] = disc_val_end
        result["oos"] = True
        result["purge_days"] = self.purge_days
        result["embargo_days"] = self.embargo_days
        return gate_metric, result


# ---------------------------------------------------------------------------
# Loader call helper
# ---------------------------------------------------------------------------

def _loader_accepts_date_kwargs(loader: Callable) -> bool:
    """Return True if *loader* accepts purge_end_date / embargo_start_date kwargs."""
    try:
        sig = inspect.signature(loader)
    except (ValueError, TypeError):
        return False
    params = sig.parameters
    # Accept if there are **kwargs or explicit keyword params with those names.
    if any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    ):
        return True
    return "purge_end_date" in params or "embargo_start_date" in params


def _call_loader(
    loader: Callable,
    start_year: int,
    end_year: int,
    loader_accepts_dates: bool,
    *,
    purge_end_date: Optional[str],
    embargo_start_date: Optional[str],
) -> Any:
    """Call data_loader with appropriate arguments depending on its signature."""
    if loader_accepts_dates:
        return loader(
            start_year,
            end_year,
            purge_end_date=purge_end_date,
            embargo_start_date=embargo_start_date,
        )
    return loader(start_year, end_year)


# ---------------------------------------------------------------------------
# Weight exporter — single consolidated function
# ---------------------------------------------------------------------------

def export_weights_to_cpp(
    weights: List[float],
    output_path: str,
    model_id: int = 1,
    feature_count: Optional[int] = None,
) -> None:
    """Export trained weights to the binary format expected by the C++ reader.

    The C++ ``DecisionEngine::load_model`` reads the file on x86/x86-64
    (little-endian) hardware using raw ``fread`` into native structs, so the
    file must be explicitly little-endian.

    Header format (16 bytes, all little-endian uint32):
        magic        0x48465433  ('HFT3')
        version      1
        model_id     caller-supplied
        feature_count number of *active* weights (derived from len(weights)
                      when not explicitly passed)

    Followed by 1024 little-endian IEEE-754 doubles, zero-padded.

    Parameters
    ----------
    weights:
        Active weight vector.  Must have len <= 1024.
    output_path:
        Destination path.
    model_id:
        Written into the header.
    feature_count:
        Number of active features recorded in the header.  When *None*
        (default), derived from ``len(weights)``.  Pass an explicit value only
        when the logical feature count differs from the weight-vector length
        (e.g. bias padding).
    """
    if len(weights) > 1024:
        raise ValueError(
            f"Model has {len(weights)} weights, exceeds C++ capacity of 1024."
        )
    if feature_count is None:
        feature_count = len(weights)

    padded = weights + [0.0] * (1024 - len(weights))

    with open(output_path, "wb") as fh:
        # Little-endian header: magic, version, model_id, feature_count.
        header = struct.pack("<IIII", 0x48465433, 1, model_id, feature_count)
        fh.write(header)
        # Little-endian doubles.
        fh.write(struct.pack(f"<{len(padded)}d", *padded))
